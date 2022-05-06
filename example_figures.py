"""Code examples for consulting the DynamoDB database for GNSS radio occultation 
data in the AWS Open Data Registry. 

Author: Stephen Leroy (sleroy@aer.com)
Date: April 28, 2022"""

#  Import python standard modules. 

import sys
from datetime import datetime, timedelta
import json

#  Import installed modules. 

import numpy as np
import cartopy
import matplotlib.pyplot as plt
from matplotlib.ticker import MultipleLocator
import boto3
from boto3.dynamodb.conditions import Key, Attr

#  Local resources. 

aws_profile = "aernasaprod"
aws_region = "us-east-1"
dynamodb_table = "gnss-ro-data-staging"

valid_missions = {
    'gpsmet': [ "gpsmet", "gpsmetas" ],
    'grace': [ "gracea", "graceb" ],
    'sacc': [ "sacc" ],
    'champ': [ "champ" ],
    'cosmic1': [ "cosmic1c{:1d}".format(i) for i in range(1,7) ],
    'tsx': [ "tsx" ],
    'tdx': [ "tdx" ],
    'cnofs': [ "cnofs" ],
    'metop': [ "metopa", "metopb", "metopc" ],
    'kompsat5': [ "kompsat5" ],
    'paz': [ "paz" ],
    'cosmic2': [ "cosmic2e{:1d}".format(i) for i in range(1,7) ]
    }

#  Matplotlib default settings. 

axeslinewidth = 0.5
plt.rcParams.update( {
  'font.family': "Times New Roman", 
  'font.size': 8, 
  'font.weight': "normal", 
  'text.usetex': True, 
  'xtick.major.width': axeslinewidth, 
  'xtick.minor.width': axeslinewidth, 
  'ytick.major.width': axeslinewidth, 
  'ytick.minor.width': axeslinewidth, 
  'axes.linewidth': axeslinewidth } )

#  Define GNSS constellations. 

valid_constellations = [ "G" ]

#  Files containing count data and mission colors. 

alldata_json_file = "count_occultations.json"
colors_json_file = "mission_colors.json"

#  Define month labeling strings. 

monthstrings = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()

#  Logger. 

import logging 
LOGGER = logging.getLogger( __name__ )


################################################################################
#  Utilities. 
################################################################################

def latlabels( lats ): 

    ylabels = []
    for lat in lats: 
        if lat < 0: 
            ylabel = "{:}$^\circ$S".format( np.abs( lat ) )
        elif lat > 0: 
            ylabel = "{:}$^\circ$N".format( np.abs( lat ) )
        else: 
            ylabel = "Eq"
        ylabels.append( ylabel )
    
    return ylabels

def get_transmitters(): 

    #  Define list of all possible transmitters. 

    transmitters = []
    for constellation in valid_constellations: 
        if constellation == "G": 
            nprns = 32
        elif constellation == "R": 
            nprns = 24
        elif constellation == "E": 
            nprns = 36
        elif constellation == "C": 
            nprns = 61
        transmitters += [ f"{constellation}{prn:02d}" for prn in range(1,nprns+1) ]

    transmitters.sort()

    return transmitters


################################################################################
#  Methods. 
################################################################################

def count_occultations( first_year, last_year ): 
    """Count occultations by mission and by month, then output to 
    alldata_json_file."""

    #  AWS access. Be sure to establish authentication for profile aws_profile 
    #  for successful use. 

    session = boto3.Session( profile_name=aws_profile, region_name=aws_region )
    resource = session.resource( "dynamodb" )
    table = resource.Table( dynamodb_table )

    #  Set up logging output. 

    handlers = [ logging.FileHandler( filename="count_occultations.log" ), 
            logging.StreamHandler( sys.stdout ) ]

    logging.basicConfig( handlers=handlers, level=logging.INFO )

    #  Get list of transmitters. 

    transmitters = get_transmitters()

    #  Initialize and loop over year-month. 

    alldata = []

    for year in range(first_year,last_year+1): 
        for month in range(1,13): 

            #  Define sort key range. 

            dtime1 = datetime( year, month, 1 )
            dtime2 = dtime1 + timedelta( days=31 )
            dtime2 = datetime( dtime2.year, dtime2.month, 1 ) - timedelta( minutes=1 )

            sortkey1 = "{:4d}-{:02d}-{:02d}-{:02d}-{:02d}".format( dtime1.year, dtime1.month, dtime1.day, dtime1.hour, dtime1.minute )
            sortkey2 = "{:4d}-{:02d}-{:02d}-{:02d}-{:02d}".format( dtime2.year, dtime2.month, dtime2.day, dtime2.hour, dtime2.minute )

            #  Initialize new year-month record. 

            rec = { 'year': year, 'month': month, 'noccs': {} }

            #  Loop over partition keys. The first element of the partition key is a 
            #  satellite identifier, which are given in the valid_missions definitions. 

            for mission, satellites in valid_missions.items(): 

                LOGGER.info( f"Working on {year=}, {month=}, {mission=}" )

                #  Initialize the occultation counter for this mission-year-month. 

                rec['noccs'].update( { mission: 0 } )

                for satellite in satellites: 
                    for transmitter in transmitters: 
                        partitionkey = f"{satellite}-{transmitter}"

                        #  Query the database and count the soundings. 

                        ret = table.query( KeyConditionExpression = Key('leo-ttt').eq(partitionkey) 
                                & Key('date-time').between( sortkey1, sortkey2 ) )
                        rec['noccs'][mission] += ret['Count'] 

            LOGGER.info( "Record: " + json.dumps( rec ) )

            #  Append the month record to the alldata list. 

            alldata.append( rec )

    with open( alldata_json_file, 'w' ) as out: 
        LOGGER.info( f"Writing data counts to {alldata_json_file}." )
        json.dump( alldata, out, indent="  " )

    return alldata


def plot_counts( epsfile ): 
    """Plot of timeseries stackplot of the counts of occultations per day by 
    mission with monthly resolution. Save encapsulated postscript file to 
    epsfile."""

    #  Read data computed previously by count_occultations.  

    with open( alldata_json_file, 'r' ) as d: 
        alldata = json.load( d )

    #  Find the start dates of counts for each mission. 

    missions = list( alldata[0]['noccs'].keys() )
    start_months = [ None for m in missions ]

    for rec in alldata: 
        for im, m in enumerate( missions ): 
            if start_months[im] is None and rec['noccs'][m] != 0: 
                start_months[im] = rec['year'] + ( rec['month'] - 0.5 ) / 12.0

    #  Eliminate missions without data. 

    smissions, sstarts = [], []
    for i, m in enumerate( missions ): 
        if start_months[i] is not None: 
            smissions.append( m )
            sstarts.append( start_months[i] )
    
    #  Sort start months. 

    isort = np.argsort( sstarts )
    missions = []
    for i in isort: 
        missions.append( smissions[i] )
    
    nmissions = isort.size

    #  Now resort the data. 

    counts = np.zeros( (nmissions, len(alldata)), dtype='f' )
    times = np.array( [ rec['year'] + ( rec['month'] - 0.5 ) / 12.0 for rec in alldata ] )

    for irec, rec in enumerate( alldata ): 
        for imission, mission in enumerate( missions ): 
            counts[imission,irec] = rec['noccs'][mission]

    #  Normalize counts by days in each month. 

    ndays = np.array( [ 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31 ], dtype='f' )
    for i, rec in enumerate( alldata ): 
        imonth = rec['month'] - 1
        counts[:,i] /= ndays[imonth]

    #  Now do the stack plot. 

    fig = plt.figure( figsize=[6,3] )
    ax = fig.add_axes( [ 0.12, 0.15, 0.86, 0.83 ] )

    #  x axis. 

    ax.set_xlim( int( times[0] ), int( times[-1] ) + 1 )
    ax.set_xticks( np.arange( int(times[0]), times[-1]+1/12.0, 5 ) )
    ax.set_xticklabels( ax.get_xticks().astype('i'), rotation=-60, fontsize="large" )
    ax.xaxis.set_minor_locator( MultipleLocator( 1.0 ) )

    #  y axis. 

    ax.set_ylabel( 'Mean daily counts', fontsize="large" )
    ax.set_ylim( 0, 5000 )
    ax.set_yticks( np.arange( 0, 5001, 1000 ) )
    ax.set_yticklabels( ax.get_yticks().astype('i'), fontsize="large" )
    ax.yaxis.set_minor_locator( MultipleLocator( 200 ) )

    #  Now, stack plot. 

    ps = ax.stackplot( times, counts, labels=missions )
    ax.legend( loc="upper left", ncol=2 )
    leg = ax.get_legend()

    #  Get colors. 

    colormap = {}
    for im, m in enumerate(missions): 
        colormap.update( { m: list( ps[im].get_facecolor()[0] ) } )

    #  Save colors for future use. 

    with open( colors_json_file, 'w' ) as c: 
        print( f"Writing mission colors to {colors_json_file}." )
        json.dump( colormap, c, indent="    " )

    #  Generate plot. 

    print( f"Saving to {epsfile}." )
    plt.savefig( epsfile, format="eps" )

    return


def distribution_solartime_figure( year, month, day, epsfile ): 
    """Create a figure showing the distribution of occultations, by mission, as is 
    typical for one day of soundings for a particular year-month. epsfile is the 
    output encapsulated postscript file."""

    #  Set up session and dynamodb table. 

    session = boto3.Session( profile_name=aws_profile, region_name=aws_region )
    resource = session.resource( "dynamodb" )
    table = resource.Table( dynamodb_table )

    #  Which missions have data for this month? 

    with open( alldata_json_file, 'r' ) as d: 
        alldata = json.load( d )

    for rec in alldata: 
        if rec['year'] == year and rec['month'] == month: 
            missions = [ m for m in rec['noccs'].keys() if rec['noccs'][m] > 0 ]
            break

    #  Get all soundings for this month by mission. 

    maprecs = []

    #  Get list of transmitters. 

    transmitters = get_transmitters()

    #  Define sort key range. 

    dtime1 = datetime( year, month, day )
    dtime2 = dtime1 + timedelta( minutes=1439 )

    sortkey1 = "{:4d}-{:02d}-{:02d}-{:02d}-{:02d}".format( dtime1.year, dtime1.month, dtime1.day, dtime1.hour, dtime1.minute )
    sortkey2 = "{:4d}-{:02d}-{:02d}-{:02d}-{:02d}".format( dtime2.year, dtime2.month, dtime2.day, dtime2.hour, dtime2.minute )

    #  Loop over missions. 

    for mission, satellites in valid_missions.items(): 

        if mission not in missions: continue

        #  For each mission-year-month, retrieve all soundings, decimate, and retain 
        #  geolocation information. 

        rec = { 'mission': mission, 'longitudes': [], 'latitudes': [], 'solartimes': [] }
        noccs = 0

        for satellite in satellites: 
            for transmitter in transmitters: 
                partitionkey = f"{satellite}-{transmitter}"

                #  Query the database. 

                ret = table.query( KeyConditionExpression = Key('leo-ttt').eq(partitionkey) 
                        & Key('date-time').between( sortkey1, sortkey2 ) )

                if ret['Count'] != 0: 
                    for item in ret['Items']: 
                        if float( item['longitude'] ) == -999.99 \
                                or float( item['latitude'] ) == -999.99 \
                                or float( item['local_time'] ) == -999.99 : 
                            continue
                        rec['longitudes'].append( float( item['longitude'] ) )
                        rec['latitudes'].append( float( item['latitude'] ) )
                        rec['solartimes'].append( float( item['local_time'] ) )
                        noccs += 1

        maprecs.append( rec )

    #  Read colormap. 

    with open( colors_json_file, 'r' ) as c: 
        colormap = json.load( c )

    #  Execute map. 

    fig = plt.figure( figsize=(6.5,2.0) )

    #  Longitude-latitude map. 

    ax = fig.add_axes( [0.01,0.22,0.45,0.67], projection=cartopy.crs.PlateCarree() )
    ax.coastlines( )

    ax.set_xlim( -180, 180 )
    ax.set_ylim( -90, 90 )

    title = "(a) RO geolocations for {:} {:} {:4d}".format( day, monthstrings[month-1], year )
    ax.set_title( title )

    #  Loop over mission records, plotting occ locations. 

    for rec in maprecs: 
        color = colormap[rec['mission']]
        ax.scatter( rec['longitudes'], rec['latitudes'], color=color, s=0.25 )

    #  Next axis: solar time distribution. 

    ax = fig.add_axes( [0.54,0.22,0.43,0.67] )

    title = "(b) RO solar times for {:} {:} {:4d}".format( day, monthstrings[month-1], year )
    ax.set_title( title )

    ax.set_xlim( 0, 24 )
    ax.set_xticks( np.arange( 0, 24.1, 6 ) )
    ax.xaxis.set_minor_locator( MultipleLocator( 1 ) )
    ax.set_xticklabels( [ f"{v:02d}:00" for v in ax.get_xticks().astype('i') ] )

    yticks = np.arange( -90, 90.1, 30 ).astype('i')
    ax.set_ylim( -90, 90 )
    ax.set_yticks( yticks )
    ax.yaxis.set_minor_locator( MultipleLocator( 10 ) )
    ax.set_yticklabels( latlabels( yticks ) )

    #  Loop over mission records, plotting occ solartimes. 

    for rec in maprecs: 
        color = colormap[rec['mission']]
        ax.scatter( rec['solartimes'], rec['latitudes'], color=color, s=0.25 )

    #  Done with figure. 

    print( f"Writing to {epsfile}." )
    fig.savefig( epsfile, format='eps' )

    return


if __name__ == "__main__": 
    import pdb
    pdb.set_trace()
    # alldata = count_occultations( 1995, 2020 )
    # plot_counts()
    # distribution_solartime_figure( 1997, 1, 10, epsfile="distribution_1997-01-10.eps" )
    # distribution_solartime_figure( 2003, 1, 2, epsfile="distribution_2003-01-02.eps" )
    distribution_solartime_figure( 2009, 1, 4, epsfile="distribution_2009-01-04.eps" )
    # distribution_solartime_figure( 2020, 1, 3, epsfile="distribution_2020-01-03.eps" )

    pass

