"""nasa_earthdata.py


Author: Stephen Leroy (sleroy@aer.com)
Date: March 1, 2024

This module contains classes and methods useful for accessing the NASA 
DAAC.  Note that you'll have to create an account on the NASA Earthdata 
server."""


import os, re, stat, json, subprocess
import earthaccess
import requests, netrc, boto3
from platform import system
from datetime import datetime, timedelta, timezone
from awsgnssroutils.collocation.core.TimeStandards import Time
from awsgnssroutils.collocation.core.constants_and_utils import defaults_file

#  Definitions. 

#  ATMS pointers are for version 3 level 1B radiances. 
#  CrIS pointers are for "full spectral resolution, version 3" level 1B radiances. 

Satellites = { 
        'Suomi-NPP': { 
                'aliases': [ "Suomi-NPP", "SNPP" ], 
                'atms': "10.5067/FCXKUUE9VCLN", 
                'cris': "10.5067/ZCRSHBM5HB23"
        }, 
        'JPSS-1': { 
                'aliases': [ "JPSS-1", "NOAA-20" ], 
                'atms': "10.5067/MUNII2DHSSY3", 
                'cris': "10.5067/LVEKYTNSRNKP" 
        }, 
        'JPSS-2': {
                'aliases': [ "JPSS-2", "NOAA-21" ] 
        }
    }

HOME = os.path.expanduser( "~" )
root_path_variable = "nasa_earthdata_root"
earthdata_machine = "urs.earthdata.nasa.gov"
time_limit = timedelta( seconds=3600 )

#  String parsing. 

file_search_string = "^SNDR\..*(\d{8}T\d{4})\.m.*\.nc$"
time_parse_string = "%Y%m%dT%H%M" 

#  Establist the netrc file name. 

if system() == "Windows": 
    netrc_file = os.path.join( HOME, "_netrc" )
else: 
    netrc_file = os.path.join( HOME, ".netrc" )

#  Define metadata on the Earthdata DAACs. 

earthdata_daacs = {
        'podaac': { 'endpoint': "https://archive.podaac.earthdata.nasa.gov/s3credentials", 
                    'region': "us-west-2" }, 
        'gesdisc': { 'endpoint': "https://data.gesdisc.earthdata.nasa.gov/s3credentials", 
                    'region': "us-west-2" } }

#  Exception handling. 

class Error( Exception ): 
    pass 

class earthdataError( Error ): 
    def __init__( self, message, comment ): 
        self.message = message
        self.comment = comment 


def setdefaults( root_path, earthdatalogin=None ): 
    """This method sets the default root path for NASA GDAAC downloads.
    Optionally, it also allows you to register your Earthdata username and 
    password in your home .netrc (if you haven't done so already).

    The Earthdata username and password can be given as a 2-tuple 
    (username,password) in optional argument earthdatalogin."""

    if os.path.exists( defaults_file ): 
        with open( defaults_file, 'r' ) as f: 
            defaults = json.load( f )
        defaults.update( { root_path_variable: root_path } )
    else: 
        defaults = {}

    with open( defaults_file, 'w' ) as f: 
        json.dump( defaults, f, indent="  " )
    os.chmod( defaults_file, stat.S_IRUSR | stat.S_IWUSR )

    if earthdatalogin is not None: 
        if isinstance(earthdatalogin,tuple) or isinstance(earthdatalogin,list): 
            if len(earthdatalogin) == 2: 

                if os.path.exists( file ): 
                    with open( netrc_file, 'r' ) as f: 
                        lines = f.readlines()
                else: 
                    lines = []
                
                #  Catalog lines according to machine. 

                catalog = {}
                for line in lines: 
                    m = re.search( "^machine\s+(\S+)", line )
                    if m: 
                        catalog.update( { m.group(1): line.strip() } )

                #  Update catalog to include earthdata. 

                catalog.update( { earthdata_machine: \
                        "machine {:} login {:} password {:}".format( earthdata_machine, *earthdatalogin ) 
                        } )

                #  Write new netrc. 

                with open( netrc_file, 'w' ) as f: 
                    for machine, line in catalog.items(): 
                        f.write( line + "\n" )

                os.chmod( netrc_file, stat.S_IRUSR | stat.S_IWUSR )

            else: 
                raise earthdataError( "InvalidArgument", "earthdatalogin must have length 2" )

        else: 
            raise earthdataError( "InvalidArgument", "earthdatalogin must be a tuple or a list" )

    #  Done. 

    return


class NASAEarthdata(): 
    """Class to handle interaction with NASA DAACs."""

    def __init__( self ): 
        """Create an instance of EUMETSATDataStore. data_root should be an 
        absolute path to the storage location for downloaded EUMETSAT Data
        Store data."""

        #  Configure credentials. 

        self.earthaccess = earthaccess.login( strategy="netrc", persist=True )

        #  Get root data path. 

        with open( defaults_file, 'r' ) as f: 
            defaults = json.load( f )
            self.data_root = defaults[root_path_variable]
            os.makedirs( self.data_root, exist_ok=True )

        #  Initialize inventory. 

        self.inventory = {}
        self.regenerate_inventory()

        return

    def regenerate_inventory( self ): 
        """Create an inventory of the files available on the local file system 
        with ATMS data (as obtained from GES DISC."""

        #  Get list of satellites. 

        instruments = [ p for p in os.listdir( self.data_root ) if os.path.isdir( os.path.join( self.data_root, p ) ) ]

        #  Loop over instruments. 

        for instrument in instruments: 

            data_root = os.path.join( self.data_root, instrument )

            #  Initialize inventory. 

            if os.path.isdir( data_root ): 
                satellites = [ p for p in os.listdir( data_root ) if p in Satellites.keys() \
                        and os.path.isdir( os.path.join( data_root, p ) ) ]
            else: 
                satellites = []

            if instrument not in self.inventory: 
                self.inventory.update( { instrument: { sat: [] for sat in satellites } } )

            #  Loop over satellites. 

            for sat in satellites: 

                for root, subdirs, files in os.walk( os.path.join( data_root, sat ) ): 
                    subdirs.sort()
                    files.sort()

                    for file in files: 
                        m = re.search( file_search_string, file )
                        if m is None: 
                            continue
                        t1 = Time( utc = datetime.strptime( m.group(1), time_parse_string ) ) 
                        t2 = t1 + 6 * 60

                        rec = { 'satellite': sat, 'path': os.path.join( root, file ), 'timerange': ( t1, t2 ) }
                        self.inventory[instrument][sat].append( rec )

        return

    def get_paths( self, satellite, instrument, timerange ): 
        """Return a listing of the paths to JPSS ATMS data files given a 
        satellite name and a time range. The timerange is a two-element 
        tuple/list with instances of TimeStandards.Time or datetime.datetime. 
        If it is the latter, then the datetime elements are understood to be 
        UTC."""

        #  Check input. Interpret datetime.datetime as TimeStandards.Time instances 
        #  if necessary. 

        if satellite not in Satellites.keys(): 
            raise earthdataError( "InvalidArgument", "The satellite must be one of " + \
                    ", ".join( list( Satellites.keys() ) ) )

        elif instrument not in Satellites[satellite].keys() or instrument == "aliases": 
            raise earthdataError( "InvalidArgument", 
                    f"The instrument {instrument} is not defined for satellite {satellite}" )

        if not isinstance( timerange, tuple ) and not isinstance( timerange, list ): 
            raise earthdataError( "InvalidArgument", "timerange must be a tuple/list of two elements" )

        if len( timerange ) != 2: 
            raise earthdataError( "InvalidArgument", "timerange must be a tuple/list of two elements" )

        if isinstance( timerange[0], datetime ) and isinstance( timerange[1], datetime ): 
            _timerange = [ Time( utc=timerange[i] ) for i in range(2) ]
        elif isinstance( timerange[0], Time ) and isinstance( timerange[1], Time ): 
            _timerange = timerange
        else: 
            raise earthdataError( "InvalidArgument", "The elements of timerange must both be " + \
                    "datetime.datetime or TimeStandards.Time" )

        ret = []
        if instrument in self.inventory.keys(): 
            if satellite in self.inventory[instrument].keys(): 
                ret = sorted( [ rec['path'] for rec in self.inventory[instrument][satellite] if \
                        rec['timerange'][0] <= _timerange[1] and rec['timerange'][1] >= _timerange[0] ] )

        return ret

    def populate( self, satellite, instrument, timerange ): 
        """Download SNPP, JPSS ATMS data that fall within a timerange. 

        * satellite must be one of Satellites.keys(). 
        * instrument is one of 'atms', 'cris'. 
        * timerange is a 2-element tuple/list of instances of TimeStandards.Time 
          or instances of datetime.datetime defining the range of times over which 
          to retrieve data. If they are instances of datetime.datetime, then the 
          convention is that they are both UTC."""

        #  Check input. Interpret datetime.datetime as TimeStandards.Time instances 
        #  if necessary. 

        if satellite not in Satellites.keys(): 
            raise earthdataError( "InvalidArgument", "The satellite must be one of " + \
                    ", ".join( list( Satellites.keys() ) ) )

        elif instrument not in Satellites[satellite].keys() or instrument == "aliases": 
            raise earthdataError( "InvalidArgument", 
                    f"The instrument {instrument} is not defined for satellite {satellite}" )

        if not isinstance( timerange, tuple ) and not isinstance( timerange, list ): 
            raise earthdataError( "InvalidArgument", "timerange must be a tuple/list of two elements" )

        if len( timerange ) != 2: 
            raise earthdataError( "InvalidArgument", "timerange must be a tuple/list of two elements" )

        if isinstance( timerange[0], datetime ) and isinstance( timerange[1], datetime ): 
            _timerange = [ Time( utc=timerange[i] ) for i in range(2) ]
        elif isinstance( timerange[0], Time ) and isinstance( timerange[1], Time ): 
            _timerange = timerange
        else: 
            raise earthdataError( "InvalidArgument", "The elements of timerange must both be " + \
                    "datetime.datetime or TimeStandards.Time" )

        #  Query the local and remote inventories. 

        local_inventory = self.get_paths( satellite, instrument, _timerange )
        etimerange = [ _timerange[0], _timerange[1] + 86400 ]
        temporal = tuple( [ t.calendar("utc").datetime().strftime("%Y-%m-%d") for t in etimerange ] ) 
        
        remote_inventory = earthaccess.search_data( doi=Satellites[satellite][instrument], temporal=temporal )

        #  Get a listing of data files that are available at Earthdata but not in the local inventory. 

        local_basenames = [ os.path.basename( p ) for p in local_inventory ]

        get = []
        for p in remote_inventory: 
            basename = os.path.basename( p.data_links()[0] )
            if basename in local_basenames: 
                continue
            m = re.search( file_search_string, basename )
            t = Time( utc=datetime.strptime( m.group(1), time_parse_string ) )
            if t+360 >= _timerange[0] and t <= _timerange[1]: 
                get.append( p )

        #  Get data files that we don't yet have. 

        if len( get ) > 0: 

            earthaccess.download( get, "tmp" )
            files = sorted( [ os.path.join( "tmp", f ) for f in os.listdir( "tmp" ) ] )

            for file in files: 

                #  Parse file name for time of granule. 

                m = re.search( file_search_string, os.path.basename(file) )
                dt = datetime.strptime( m.group(1), time_parse_string )

                #  Define local path for file. 

                lpath = os.path.join( self.data_root, instrument, satellite, 
                            f'{dt.year:02d}', f'{dt.month:02d}', f'{dt.day:02d}', 
                            os.path.basename( file ) )

                #  Move file. 

                os.makedirs( os.path.dirname( lpath ), exist_ok=True )
                os.link( file, lpath )
                os.unlink( file )

        #  Regenerate inventory. 

        self.regenerate_inventory()

        return 

