"""metop_amsua.py

Authors: Meredith, Stephen Leroy
Contact: Stephen Leroy (sleroy@aer.com)
Date: March 5, 2024

This contains the definition of an ATMS instrument class on board 
a JPSS satellite --- including Suomi-NPP, JPSS-1 (NOAA-20), JPSS-2 
(NOAA-21), etc. --- with data hosted in NASA Earthdata DAACs. The class 
provided is named by class_name, which inherits the NadirSatelliteInstrument
class. 
"""

from collocation.core.nadir_satellite import NadirSatelliteInstrument, ScanMetadata
from netCDF4 import Dataset
import numpy as np
from awsgnssroutils.collocation.core.TimeStandards import Time, Calendar
from awsgnssroutils.collocation.core.eumetsat import eumetsat_time_convention
from awsgnssroutils.collocation.core.constants_and_utils import masked_dataarray, \
        planck_blackbody, speed_of_light 
from datetime import datetime
import xarray 

#  REQUIRED attribute

instrument_name = "ATMS"
instrument_aliases = [ "ATMS", "atms", "JPSS ATMS" ]

#  REQUIRED methods of the class: 
#   - get_geolocations
#   - get_data


#  Parameters. 

fill_value = -1.0e20

#  Exception handling. 

class Error( Exception ): 
    pass

class metop_amsua_error( Error ): 
    def __init__( self, message, comment ): 
        self.message = message
        self.comment = comment


#  Buffer for data files. 

open_data_file = { 'path': None, 'pointer': None }


class instrument(NadirSatelliteInstrument):
    """JPSS/Suomi-NPP ATMS satellite instrument object, inheriting from 
    NadirSatelliteInstrument. This represents an ATMS scanner aboard a 
    Suomi-NPP/JPSS satellite.

    Parameters
    ------------
        name: str
            Name of the nadir satellite, drawn from collocation.core.celestrak.Satellites[:]['name']
        nasa_earthdata_access: collocation.core.nasa_earthdata.NASAEarthdata
            An object that interfaces with the NASA Earthdata DAACs. 
        celestrak: collocation.core.celestrak.Celestrak
            Portal to the Celestrak TLE data

    Attributes
    ------------
        name: str
            Name of the nadir satellite, drawn from collocation.core.celestrak.Satellites[:]['name']
        celestrak_satellite: instance of celestrak.CelestrakSatellite
            Define the satellite, for access to TLEs
        xi: float
            MWR instrument maximum scan angle [radians]
        data: string
            Filepath to sounding data
        time_between_scans: float
            Time between cross-track scans [seconds]
        scan_points_per_line: int
            Number of individual footprints per cross-track scan
        scan_angle_spacing: float
            Angle between scan footprints [radians]
        eumetsat_access: collocation.core.eumetsat.EUMETSATDataStore
            An object that interfaces with the EUMETSAT Data Store. 
        celestrak_satellite: collocation.core.celestrak.CelestrakSatellite
            TLE data for the satellite
    """

    def __init__(self, name, nasa_earthdata_access, celestrak=None ):
        """Constructor for MetopAMSUA class."""

        max_scan_angle = 52.63              # degrees
        time_between_scans = 8.0/3          # seconds
        scan_points_per_line = 96           # footprints in a scan
        scan_angle_spacing = 1.108          # degrees

        super().__init__( name, max_scan_angle, time_between_scans,
                scan_points_per_line, scan_angle_spacing, celestrak=celestrak )

        self.nasa_earthdata_access = nasa_earthdata_access

        pass

    def populate( self, timerange ): 
        """Populate (download) all JPSS ATMS data for this satellite in a 
        time range defined by timerange. timerange can be a 2-tuple/list of 
        instances of TimeStandards.Time or datetime.datetime with the 
        understanding that the latter is defined as UTC times."""

        self.nasa_earthdata_access.populate( self.name, 'atms', timerange )
        return

    def get_geolocations_from_file( self, filename ):
        """Get geolocation information from a single input file filename."""

        #  Define time standard for timing information in EUMETSAT Data Store 
        #  AMSU-A level 1a files. Possibilities are "utc", "tai", "gps". 

        #  Open file. 

        d = Dataset( filename, 'r' )

        #  Get dimensions. 

        nx = d.dimensions['xtrack'].size
        ny = d.dimensions['atrack'].size

        #  Get longitudes and latitudes. Convert to radians. 

        latitudes = np.deg2rad( d.variables['lat'][:] )
        longitudes = np.deg2rad( d.variables['lon'][:] )

        #  Get start and stop time of scans in file. 

        xtuples = d.variables['obs_time_utc'][:,int(nx/2),:]
        mid_times = [ Time( utc=Calendar( *( xtuples[iscan,0:6] ) ) ) \
                + xtuples[iscan,6]*1.0e-3 + xtuples[iscan,7]*1.0e-6 \
                for iscan in range(ny) ]

        d.close()

        return { 'longitudes': longitudes, 'latitudes': latitudes, 'mid_times': mid_times }

    def get_geolocations( self, timerange ):
        """Load AMSU-A data from a Metop satellite as obtained from the EUMETSAT Data Store
        using collocation.core.eumetsat.EUMETSATDataStore.satellite.populate_metop_amsua. The 
        timerange is a two-element tuple/list containing instances of TimeStandards.Time 
        that prescribe the time range over which to obtain AMSU-A geolocations. An instance 
        of class ScanMetadata containing the footprint geolocations is returned upon successful 
        completion."""

        #  The EUMETSAT data is stored by orbit. In order to find all relevant AMSU-A 
        #  soundings, be sure to subtract one orbital period from the first time and 
        #  add one orbital period to the last time. 

        data_files = self.nasa_earthdata_access.get_paths( self.name, 'atms', timerange )
        gps0 = Time(gps=0)
        dt = ( timerange[0]-gps0, timerange[1]-gps0 )

        #  Loop over data files. Keep geolocations only for soundings within the timerange. 
        #  Initialize geolocation variables. 

        longitudes, latitudes, mid_times, scan_indices, file_indices = [], [], [], [], [] 

        for ifile, data_file in enumerate(data_files): 

            #  Read all geolocation information from data_file. 

            ret = self.get_geolocations_from_file( data_file )

            #  Convert mid_times to an np.ndarray of GPS times.  Find times for soundings 
            #  within the prescribed timerange. 

            file_gps_times = np.array( [ t-gps0 for t in ret['mid_times'] ] )
            good = np.argwhere( np.logical_and( dt[0] <= file_gps_times, file_gps_times < dt[1] ) ).flatten()

            #  Keep only those soundings within the prescribed timerange. 

            if good.size > 0: 
                longitudes += [ ret['longitudes'][iy,:] for iy in good ]
                latitudes += [ ret['latitudes'][iy,:] for iy in good ]
                mid_times += [ Time(gps=file_gps_times[iy]) for iy in good ]
                scan_indices += list( good )
                file_indices += [ifile] * good.size

        #  Convert to ndarrays. 

        longitudes = np.array( longitudes )
        latitudes = np.array( latitudes )

        #  Generate output object. 

        ret = ScanMetadata( self.get_data, longitudes, latitudes, mid_times, data_files, file_indices, scan_indices )

        return ret

    def get_data( self, file, scan_index, footprint_index, 
                 longitude=None, latitude=None, time=None ): 
        """A function which returns nadir-scan satellite data for requested scan and 
        footprint indices within the file *file*. The function itself must be a 
        Method to fetch data for a nadir-scan satellite instrument corresponding to 
        a scalar integer indicating the scan number [0:nscans] and a scalar integer 
        indicating the footprint number [0:nfootprints].

        It returns an xarray.Dataset according taken from file *file* and the 
        data location within the file should correspond to scans 
        data[scan_index,footprint_index].

        Longitude (degrees), latitude (degrees), and time (TimeStandards.Time) will 
        be included in the returned xarray.Dataset if provided."""

        #  Check input. 

        integer_types = [ int, np.int8, np.int16, np.int32, np.int64 ]

        if not isinstance(file,str): 
            raise metop_amsua_error( "InvalidArgument", "file argument must be type str" )

        if not any( [ isinstance(scan_index,t) for t in integer_types ] ): 
            raise metop_amsua_error( "InvalidArgument", "scan_index argument must be an integer type" )

        if not any( [ isinstance(footprint_index,t) for t in integer_types ] ): 
            raise metop_amsua_error( "InvalidArgument", "footprint_index argument must be an integer type" )

        #  Open data file. 

        global open_data_file

        if open_data_file['pointer'] is not None: 
            if open_data_file['path'] == file: 
                #  Data file already open. 
                d = open_data_file['pointer']
            else: 
                #  Close previously opened file. 
                open_data_file['pointer'].close()
                open_data_file['path'] = None
                open_data_file['pointer'] = None

        if open_data_file['path'] is None: 
            #  Open new file. 
            open_data_file['path'] = file
            open_data_file['pointer'] = Dataset( file, 'r' )
            d = open_data_file['pointer']

        dim_nscans, dim_nfootprints = d.dimensions['atrack'].size, d.dimensions['xtrack'].size
        nchannels = d.dimensions['channel'].size  

        #  Get data values. 

        brightness_temperature = d.variables['antenna_temp'][scan_index,footprint_index,:].flatten()

        #  Convert brightness tempereature to radiance. Convert radiances from 
        #  W m**-2 Hz**-1 ster**-1 to mW m**-2 (cm**-1)**-1 ster**-1. 

        frequencies = d.variables['center_freq'][:] * 1.0e6     #  Convert to Hz. 
        radiances = planck_blackbody( frequencies, brightness_temperature ) \
                * 1.0e3 * speed_of_light * 100.0 

        zenith = d.variables['sat_zen'][scan_index,footprint_index]

        #  Convert to np.ndarrays. 

        radiance_dataarray = masked_dataarray( radiances, fill_value, 
                dims=("channel",), 
                coords = { 'channel': np.arange(nchannels,dtype=np.int32)+1 } )
        radiance_dataarray.attrs.update( {
            'description': "Microwave radiance from ATMS instrument", 
            'units': "mW m**-2 (cm**-1)**-1 steradian**-1" } )

        zenith_dataarray = masked_dataarray( zenith, fill_value )
        zenith_dataarray.attrs.update( {
            'description': "Zenith angle from surface to satellite", 
            'units': "degrees" } )

        ds_dict = { 
            'data': radiance_dataarray, 
            'zenith': zenith_dataarray } 

        ds_attrs_dict = { 
            'satellite': self.name, 
            'instrument': "ATMS", 
            'data_file_path': file, 
            'scan_index': np.int16( scan_index ), 
            'footprint_index': np.int16( footprint_index ) } 

        if longitude is not None: 
            longitude_dataarray = xarray.DataArray( longitude )
            longitude_dataarray.attrs.update( { 
                'description': "Longitude of sounding, eastward", 
                'units': "degrees" } )
            ds_dict.update( { 'longitude': longitude_dataarray } )

        if latitude is not None: 
            latitude_dataarray = xarray.DataArray( latitude )
            latitude_dataarray.attrs.update( { 
                'description': "Latitude of sounding, northward", 
                'units': "degrees" } )
            ds_dict.update( { 'latitude': latitude_dataarray } )

        if time is not None: 
            ds_dict.update( { 'time': time.calendar("utc").isoformat(timespec="milliseconds")+"Z" } )

        ds = xarray.Dataset( ds_dict )
        ds.attrs.update( ds_attrs_dict )

        return ds

