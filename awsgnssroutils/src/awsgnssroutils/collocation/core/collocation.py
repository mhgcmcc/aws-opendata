"""collocation.py

Author: Stephen Leroy (sleroy@aer.com)
Version: 
Date: March 13, 2024

This module establishes two classes to handle radio occultation--nadir scanner 
collocation data: class Collocation and class CollocationList. In addition it 
provides a method to be used internally that generates xarray DataArrays that 
handle fill values properly.

class Collocation:
    Define a radio occultation - scanner data collocation. It contains all 
    of the metadata necessary to identify the individual soundings. It also 
    contains methods to fill extract the actual data/observations associated 
    with the occultation and nadir scanner soundings.

class CollocationList: 
    Define a list of instances of Collocation. It inherits the list class and 
    adds methods to form unions and intersections of instances of 
    CollocationList. It also includes methods to sort the list and write its 
    contents to an output NetCDF file."""


#  Imports. 

import os, json
import numpy as np
import xarray
import netCDF4 
from awsgnssroutils.database import OccList
from .timestandards import Time
from .nadir_satellite import NadirSatelliteInstrument, ScanMetadata
from .constants_and_utils import masked_dataarray

#  Exception handling. 

class Error( Exception ): 
    pass

class collocationError( Error ): 
    def __init__( self, message, comment ): 
        self.message = message
        self.comment = comment

#  Internal. 

__version__ = "1.0.0"

#  Parameters. 

fill_value = -1.0e20


#  Collocation class definition. 

class Collocation(): 
    """A class that defines an existing collocation. Its attributes are 

    occultation -> An OccList of size; it contains all metadata associated with 
            the collocated occultation. 

    nadir_satellite -> An instance of NadirSatelliteInstrument that contains 
            information on satellite and instrument name along with information on 
            the satellite's TLE's. 

    longitude -> Longitude (degrees east) of the collocated nadir-scanner sounding. 

    latitude -> Latitude (degrees north) of the collocated nadir-scanner sounding. 

    time -> An instance of timestandards.Time of the collocated nadir-scanner 
            sounding; this can be approximate value if iscan, ifootprint, or 
            scan_metadata is not provided.

    scan_metadata -> An instance of ScanMetadata containing metadata on the nadir-scanner 
            observations; it is accessed when retrieve measurement data. It 
            can be an approximate value if iscan, ifootprint, or scan_metadata is not provided.

    scan_angle -> The scan angle (degrees) of the collocated nadir-scanner sounding; if it 
            is not provided, then it will be impossible to infer iscan and ifootprint. 

    iscan -> Integer (zero-based) pointing to the scan index of scan_metadata defining the 
            collocated nadir-scanner sounding. 

    ifootprint -> Integer (zero-based) pointing to the footprint index of scan_metadata 
            defining the collocated nadir-scanner sounding. 

    Note the the rotation-collocation method can determine if an occultation-nadir-scanner 
    collocation exists, but it can only give approximate pointers (iscan, ifootprint) to 
    the actual collocated nadir-scanner sounding. The actual pointers must be determined 
    by estimation and refinement given approximate values of time and scan_angle. 
    """

    def __init__( self, occultation, nadir_satellite, longitude=None, latitude=None, time=None, 
                 scan_metadata=None, scan_angle=None, iscan=None, ifootprint=None ): 

        #  Check input. 

        integer_classes = [ int, np.byte, np.int8, np.int16, np.int32, np.int64 ] 

        if not isinstance( occultation, OccList ): 
            raise collocationError( "InvalidArgument", "First argument must be an instance of OccList" )
        elif occultation.size != 1: 
            raise collocationError( "InvalidArgument", "Occultation argument must be an OccList of size 1" )

        if not isinstance( nadir_satellite, NadirSatelliteInstrument ): 
            raise collocationError( "InvalidArgument", "Second argument must be an instance of NadirScanner" )

        x = longitude
        if x is not None: 
            if not isinstance(x,float) and not isinstance(x,np.float32) and not isinstance(x.np.float64): 
                raise collocationError( "InvalidArgument", "longitude must be a float-type object" )

        x = latitude
        if x is not None: 
            if not isinstance(x,float) and not isinstance(x,np.float32) and not isinstance(x.np.float64): 
                raise collocationError( "InvalidArgument", "latitude must be a float-type object" )

        x = time
        if x is not None: 
            if not isinstance(x,Time): 
                raise collocationError( "InvalidArgument", "time must be an instance of timestandards.Time" )

        x = scan_metadata
        if x is not None: 
            if not isinstance(x,ScanMetadata): 
                raise collocationError( "InvalidArgument", "scan_metadata must be an instance of ScanMetadata" )

        x = scan_angle
        if x is not None: 
            if not isinstance(x,float) and not isinstance(x,np.float32) and not isinstance(x.np.float64): 
                raise collocationError( "InvalidArgument", "scan_angle must be a float-type object" )

        x = iscan
        if x is not None: 
            if not any( [ isinstance( x, dtype ) for dtype in integer_classes ] ): 
                raise collocationError( "InvalidArgument", "iscan must be an integer-type object" )
            if iscan < 0: 
                raise collocationError( "InvalidArgument", "iscan must be greater than or equal to 0" )

        x = ifootprint
        if x is not None: 
            if not any( [ isinstance( x, dtype ) for dtype in integer_classes ] ): 
                raise collocationError( "InvalidArgument", "ifootprint must be an integer-type object" )
            if ifootprint < 0: 
                raise collocationError( "InvalidArgument", "ifootprint must be greater than or equal to 0" )

        if scan_metadata is not None: 
            nscans, nfootprints = scan_metadata.longitudes.shape
            if iscan is not None: 
                if iscan >= nscans: 
                    raise collocationError( "InvalidArgument", f"iscan must be less than {nscans}" )
            if ifootprint is not None: 
                if ifootprint >= nfootprints: 
                    raise collocationError( "InvalidArgument", f"ifootprint must be less than {nfootprints}" )
        
        #  Define attributes. 

        self.occultation = occultation
        self.nadir_satellite = nadir_satellite
        self.longitude = longitude
        self.latitude = latitude
        self.time = time
        self.scan_metadata = scan_metadata
        self.scan_angle = scan_angle
        self.iscan = iscan
        self.ifootprint = ifootprint
        self.data = None

    def refine_scanner_indices( self ): 
        """Find the actual nadir-scanner sounding that is closest in space to the 
        occultation."""

        #  Check for necessary information. 

        if self.scan_angle is None: 
            raise collocationError( "MissingData", "scan_angle must be provided in " + \
                    "order to infer iscan, ifootprint" )

        if self.time is None: 
            raise collocationError( "MissingData", "time of collocated nadir-scanner " + \
                    "sounding must be provided in order to infer iscan, ifootprint" )

        #  Get scan data if needed. 

        if self.scan_metadata is None: 

            timerange = ( self.time - 4*self.nadir_satellite.time_between_scans, 
                         self.time + 4*self.nadir_satellite.time_between_scans )

            self.scan_metadata = self.nadir_satellite.get_geolocations( timerange )

        nscans, nfootprints = self.scan_metadata.longitudes.shape

        #  Establish position of occultation, position of nadir-scanner soundings. 

        lon, lat = np.deg2rad( self.occultation.values("longitude")[0] ), np.deg2rad( self.occultation.values("latitude")[0] )
        p_occ = np.array( [ np.cos(lon)*np.cos(lat), np.sin(lon)*np.cos(lat), np.sin(lat) ] )

        lons, lats = self.scan_metadata.longitudes, self.scan_metadata.latitudes
        p_sounder = np.array( [ np.cos(lons)*np.cos(lats), np.sin(lons)*np.cos(lats), np.sin(lats) ] ).transpose( (1,2,0) )

        #  Find minimum distance. Find actual ifootprint, iscan of closest nadir-scanner sounding. 

        distances = np.arccos( np.inner( p_sounder, p_occ ) )
        i = distances.argmin()
        self.iscan = int( i / nfootprints )
        self.ifootprint = i % nfootprints

        self.longitude = np.rad2deg( lons[ self.iscan, self.ifootprint ] )
        self.latitude = np.rad2deg( lats[ self.iscan, self.ifootprint ] )

        #  Done. 

        return self

    def get_data( self, ro_processing_center ): 
        """Get occultation and nadir-scanner data. 

        Arguments
        ---------
        ro_processing_center: str
            The name of the RO processing center to use as the source of occultation data

        Returns
        ---------
        A dictionary with the following keyword-value pairs: 
            occid -> occultation identifier
            occultation -> An xarray.Dataset containing radio occultation profile data
            sounder -> An xarray.Dataset containing nadir-scanner sounding data """

        #  Download occultation data file. 

        occ_files = self.occultation.download( f"{ro_processing_center}_refractivityRetrieval", silent=True )
        if len( occ_files ) != 1: 
            raise collocationError( "InvalidOccultation", 
                    f"Unable to obtain {ro_processing_center}_refractivityRetrieval for collocated occultation" )
        occ_file = occ_files[0]

        #  Initialize output dictionary. 

        ret = {}

        #  Extract occultation data. 

        d = netCDF4.Dataset( occ_file, 'r' )

        longitude_dataarray = xarray.DataArray( self.occultation.values("longitude")[0] )
        longitude_dataarray.attrs.update( { 
            'description': "Longitude of radio occultation sounding, eastward", 
            'units': "degrees" } )

        latitude_dataarray = xarray.DataArray( self.occultation.values("latitude")[0] )
        latitude_dataarray.attrs.update( { 
            'description': "Latitude of radio occultation sounding, northward", 
            'units': "degrees" } )

        bendingangle_dataarray = masked_dataarray( d.variables['bendingAngle'][:], fill_value, 
            dims=("impactParameter",) )
        bendingangle_dataarray.attrs.update( { 
            'description': "Bending angle, ionosphere calibrated, unoptimized", 
            'units': "radians" } )

        impactparameter_dataarray = masked_dataarray( d.variables['impactParameter'][:], fill_value, 
            dims=("impactParameter",) )
        impactparameter_dataarray.attrs.update( { 
            'description': "Impact parameter of ray", 
            'units': "meters" } )

        radiusofcurvature_dataarray = xarray.DataArray( d.variables['radiusOfCurvature'].getValue() )
        radiusofcurvature_dataarray.attrs.update( { 
            'description': "Local radius of curvature of the Earth", 
            'units': "meters" } )

        refractivity_dataarray = masked_dataarray( d.variables['refractivity'][:], fill_value, 
            dims=("altitude",) )
        refractivity_dataarray.attrs.update( { 
            'description': "Refractivity", 
            'units': "N-units" } )

        geopotential_dataarray = masked_dataarray( d.variables['geopotential'][:], fill_value, 
            dims=("altitude",) )
        geopotential_dataarray.attrs.update( { 
            'description': "Geopotential energy per unit mass", 
            'units': "J/kg" } )

        altitude_dataarray = masked_dataarray( d.variables['altitude'][:], fill_value, 
            dims=("altitude",) )
        altitude_dataarray.attrs.update( { 
            'description': "Altitude above mean sea-level geoid", 
            'units': "meters" } )

        ds_occultation = xarray.Dataset( { 
            'longitude': longitude_dataarray, 
            'latitude': latitude_dataarray, 
            'bendingAngle': bendingangle_dataarray, 
            'impactParameter': impactparameter_dataarray, 
            'radiusOfCurvature': radiusofcurvature_dataarray, 
            'refractivity': refractivity_dataarray, 
            'geopotential': geopotential_dataarray, 
            'altitude': altitude_dataarray } )

        ds_occultation.attrs.update( {
            'file': occ_files[0], 
            'mission': self.occultation._data[0]['mission'], 
            'transmitter': self.occultation._data[0]['transmitter'], 
            'receiver': self.occultation._data[0]['receiver'], 
            'time': self.time.calendar("utc").isoformat(timespec="seconds")+"Z" } )

        d.close()

        #  Find the actual closest nadir-scanner sounding. 

        if self.iscan is None or self.ifootprint is None: 
            self.refine_scanner_indices()

        self.time = self.scan_metadata.mid_times[self.iscan] 

        #  Extract sounder data. 

        ds_sounder = self.scan_metadata( self.iscan, self.ifootprint, 
            longitude=self.longitude, latitude=self.latitude, time=self.time )

        #  Merge occultation and sounder data. 

        ds = { 'occultation': ds_occultation, 'sounder': ds_sounder, 
                'occid': self.occultation._data[0]['occid'] } 

        #  Done. 

        self.data = ds

        return ds

def collocation_confusion( occs, collocations_bruteforce, collocations_rotation ): 
    """Produce a confusion matrix for the rotation-collocation algorithm. The first 
    argument must be an OccList of the occultations used for finding collocated nadir 
    soundings. The second argument must be a list of Collocation instances obtained 
    by the brute force algorithm; and the third must be a list of Collocation 
    instances obtained by the rotation-collocation algorithm. A dictionary of 
    "true_positive", "true_negative", "false_positive", "false_negative" counts is 
    produced."""

    #  Check input. 

    if not isinstance( collocations_bruteforce, list ) or not isinstance( collocations_rotation, list ): 
        raise collocationError( "InvalidArgument", "Both arguments must be lists" )

    if not isinstance( collocations_bruteforce[0], Collocation ): 
        raise collocationError( "InvalidArgument", "First argument must be a list of instances of Collocation" )

    if not isinstance( collocations_rotation[0], Collocation ): 
        raise collocationError( "InvalidArgument", "First argument must be a list of instances of Collocation" )

    n_total = occs.size
    n_in_bruteforce = len( collocations_bruteforce )
    n_in_rotation = len( collocations_rotation )
    n_in_both = len( collocations_bruteforce.intersection( collocations_rotation ) ) 

    ret = { 'true_positive': n_in_both, 
           'false_negative': n_in_bruteforce - n_in_both, 
           'false_positive': n_in_rotation - n_in_both, 
           'true_negative': n_total - ( n_in_bruteforce + n_in_rotation - n_in_both ) }

    return ret


class CollocationList( list ): 
    """A list of collocations. While inheriting the list class, it adds methods to 
    perform unions, intersections, and writing to output."""

    def __init__( self, input_list:list ): 
        """The input should be a list of instances of class Collocation."""

        if not isinstance( input_list, list ): 
                raise collocationError( "InvalidArgument", "Argument of CollocationList must be a list" )

        for element in input_list: 
            if not isinstance( element, Collocation ): 
                raise collocationError( "InvalidArgument", 
                        "All elements of the input list to CollocationList must be instances of Collocation" )

        super().__init__( input_list )

        return


    def union( self, union_list ): 
        """Return the union with the argument (instance of CollocationList)."""

        #  Check input. 

        if not isinstance( union_list, CollocationList ): 
            raise collocationError( "InvalidArgument", "Argument to CollocationList.union must be a CollocationList" )

        #  Create a dictionary corresponding to both lists. 

        dict1 = { c.occultation._data[0]['occid']: c for c in self }
        dict2 = { c.occultation._data[0]['occid']: c for c in union_list }

        #  Get union of "occid" keys. 

        keys = sorted( list( set( dict1.keys() ).union( set( dict2.keys() ) ) ) )

        #  Generate the result. 

        ret = CollocationList( [ dict1[k] for k in keys ] )

        #  Done. 

        return ret

    def intersection( self, union_list ): 
        """Return the intersection with the argument (instance of CollocationList)."""

        #  Check input. 

        if not isinstance( union_list, CollocationList ): 
            raise collocationError( "InvalidArgument", "Argument to CollocationList.intersection must be a CollocationList" )

        #  Create a dictionary corresponding to both lists. 

        dict1 = { c.occultation._data[0]['occid']: c for c in self }
        dict2 = { c.occultation._data[0]['occid']: c for c in union_list }

        #  Get intersection of "occid" keys. 

        keys = sorted( list( set( dict1.keys() ).intersection( set( dict2.keys() ) ) ) )

        #  Generate the result. 

        ret = CollocationList( [ dict1[k] for k in keys ] )

        #  Done. 

        return ret

    def write_to_netcdf( self, outputfile, author=None ): 
        """Write a list of collocations to an output NetCDF-4 file. The collocations 
        must be a list of instances of Collocation, and the file is the path of the 
        output file. Giving the author's name is optional."""

        #  Check input. 

        if not isinstance( outputfile, str ): 
            raise collocationError( "InvalidArgument", "The second argument must be a str, designating the path of the output file" )

        #  Create output file. 

        d = netCDF4.Dataset( outputfile, 'w' )

        #  Global attributes. 

        d.setncatts( { 
            'creation_time': Time().calendar("utc").isoformat(timespec="seconds")+"Z", 
            'file_type': "gnssro-nadirsounder-collocations" 
            } )

        if author is not None: 
            d.setncattr( "author", author )

        #  Loop over collocations. 

        for collocation in self: 

            if collocation.data is None: 
                ret = collocation.get_data()

            #  Write occultation and sounder data. 

            occid = collocation.data['occid'] 
            collocation_name = occid + "+" + \
                    collocation.data['sounder'].attrs['satellite'] + "-" + \
                    collocation.data['sounder'].attrs['instrument']

            occultation_group = d.createGroup( f'{collocation_name}/occultation' )
            write_dataset_to_netcdf( collocation.data['occultation'], occultation_group )

            sounder_group = d.createGroup( f'{collocation_name}/sounder' )
            write_dataset_to_netcdf( collocation.data['sounder'], sounder_group )

        #  Done. 

        d.close()

        return

    ############################################################
    #  Magic methods. 
    ############################################################

    def __getitem__( self, ss ): 
        items = list( self ).__getitem__(ss)
        if isinstance( items, Collocation ): 
            ret = items
        else: 
            ret = CollocationList( items )

        return ret

    def __sort__( self, method="occtime" ): 

        if method == "occid": 
            sdict = { c.data['occid']: c for c in self } 

        elif method == "occtime": 
            sdict = {}
            for element in self: 
                time = c.data['occultation'].attrs['time']
                for ikeychar in range(26): 
                    key = time + chr( ord("a") + ikeychar )
                    if key not in sdict.keys(): 
                        break
                sdict.update( { key: element } )

        else: 
            raise collocationError( "InvalidArgument", f'Unrecognized method {method} to sort a CollocationList' )

        keys = sorted( list( sdict.keys() ) )
        ret = CollocationList( [ sdict[key] for key in keys ] )

        return ret


def write_dataset_to_netcdf( dataset, nc ): 
    """Write an xarray Dataset (dataset) to an open NetCDF file or group (nc)."""

    #  Check input. 

    if not isinstance( dataset, xarray.Dataset ): 
        raise collocationError( "InvalidArgument", "First argument must be an instance of xarray.Dataset" )

    if not isinstance( nc, netCDF4.Dataset ): 
        raise collocationError( "InvalidArgument", "Second argument must be an instance or child of netCDF4.Dataset" )

    #  Create dimensions. 

    for name, size in dataset.sizes.items(): 
        nc.createDimension( name, size )

    #  Create variables and their attributes. 

    variables = {}
    for vname, vobj in dataset.variables.items(): 
        v = nc.createVariable( vname, vobj.dtype, vobj.dims )
        v.setncatts( vobj.attrs )
        variables.update( { vname: v } )

    #  Create global attributes. 

    nc.setncatts( dataset.attrs )

    #  Write data values. 

    for vname, vobj in variables.items(): 
        vobj[:] = dataset.variables[vname].values

    #  Done. 

    return

