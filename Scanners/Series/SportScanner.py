#
# Most code here is copyright (c) 2010 Plex Development Team. All rights reserved.
#

import re, os, os.path, random, urllib2
import Media, VideoFiles, Stack, Utils

sports_regexps = [
    '(?P<show>.*?)[^0-9a-zA-Z]+(?P<year>[0-9]{4})[^0-9a-zA-Z]+(?P<month>[0-9]{2})[^0-9a-zA-Z]+(?P<day>[0-9]{2})[^0-9a-zA-Z]+(?P<title>.*)\.[^\.]+?$',
  ]

sport_dict = { "Football" : "Soccer", "NHL" : "IceHockey", "Racing" : "Motorsport"}
seasons = { "Soccer" : { "split_date" : [7,1], "season_format" : "xxyy"},
            "IceHockey" : { "split_date" : [7,1], "season_format" : "xxyy"},
            "Motorsport" : { "season_format" : "yyyy"}}

# MIN_RETRY_TIMEOUT = 2
# RETRY_TIMEOUT = MIN_RETRY_TIMEOUT
# TOTAL_TRIES   = 1
# BACKUP_TRIES  = -1
#
# headers = {'User-agent': 'Plex/Nine'}
#
# SPORTSDB_ROOT = "http://www.thesportsdb.com/api/v1/json/1/"
#
# def GetResultFromNetwork(url, fetchContent=True):
#   global RETRY_TIMEOUT
#   try:
#     print "SS: Retrieving URL: {0}".format(url)
#
#     try:
#       req = urllib2.Request(url, None, headers)
#       response = urllib2.urlopen(req)
#       result = response.read()
#       if fetchContent:
#         return result
#     except Exception, e:
#       print "SS: Exception - {0}".format(e.message)
#
#   finally:
#     print "SS: Not returning any values"
#
#   return None


# Look for episodes.
def Scan(path, files, mediaList, subdirs):

  print "SS: Starting scan"
  #print "SS: path |", path, "|"
  #print "SS: files |", files, "|"
  #print "SS: subdirs |", subdirs, "|"
  
  # Scan for video files.
  VideoFiles.Scan(path, files, mediaList, subdirs)

  #print "SS: files |", files, "|"
  #print "SS: subdirs |", subdirs, "|"

  # Here we have only video files in files, path is only the TLD, media is empty, subdirs is populated

  # Take top two as show/season, but require at least the top one.
  paths = Utils.SplitPath(path)

  #print "SS: paths |", ", ".join(paths)
  
  if len(paths) == 1 and len(paths[0]) == 0:

    print "SS: In TLD: len(paths) == 1 and len(paths[0]) == 0"
    print "SS: Nothing interesting here, carrying on"
  
  elif len(paths) > 0 and len(paths[0]) > 0:
    done = False

    if path in sport_dict:
      sport = sport_dict[path]
      print "SS: Setting sport to {0}".format(sport)
    else:
      sport = path
    #Check every file to see if it matches what we want
    for i in files:
      file = os.path.basename(i)
      (file, ext) = os.path.splitext(file)
      print "SS: Working on file |", file, "| ext |", ext, "|"
      # Minor cleaning on the file to avoid false matches on H.264, 720p, etc.
      whackRx = ['([hHx][\.]?264)[^0-9].*', '[^[0-9](720[pP]).*', '[^[0-9](1080[pP]).*', '[^[0-9](480[pP]).*']
      for rx in whackRx:
        file = re.sub(rx, ext, file)
      for rx in sports_regexps:
        match = re.search(rx, file, re.IGNORECASE)
        if match:
          print "SS: matched regex |", rx, "|"
          year = match.group('year')
          month = int(match.group('month'))
          day = int(match.group('day'))
          show = match.group('show')
          show = re.sub( '[^0-9a-zA-Z]+', ' ', show)
          title = re.sub( '[^0-9a-zA-Z]+', ' ', match.group('title'))
          title = "{0}: {1}".format(sport, title)
          if sport in seasons:
            season_type = seasons[sport]['season_format']
            if season_type == "xxyy":
              dates = seasons[sport]['split_date']
              if month < dates[0] or (month == dates[0] and day < dates[1]):
                short_year = year[-2:]
                year_before = str(int(short_year)-1)
                season = int("{0}{1}".format(year_before, short_year))
              else :
                short_year = year[-2:]
                year_after = str(int(short_year)+1)
                season = int("{0}{1}".format(short_year, year_after))
            else:
              season = int(year)
          else:
            season = int(year)
          # See if we can match based on the date that we have, it's the most reliable info we have
          # url = "{0}eventsday.php?d={1}-{2}-{3}&s={4}".format(SPORTSDB_ROOT, year, month, day, sport)
          # JSON_results = GetResultFromNetwork(url, True)
          # print "SS: {0}".format(JSON_results)

          ep = int('%02d%02d%04d' % (month, day, (random.randint(0,9999))))
          # Use the year as the season
          tv_show = Media.Episode(show, season, ep, title, int(year))
          tv_show.released_at = '%s-%02d-%02d' % (year, month, day)
          tv_show.parts.append(i)
          mediaList.append(tv_show)

          done = True
          break

      if done == False:
        print "SS: Trying to match based off directory"

      if done == False:
        print "SS: Trying to match multipart"

      if done == False:
        print "SS: Got nothing for:", file
          
  # Stack the results.
  Stack.Scan(path, files, mediaList, subdirs)

import sys
    
if __name__ == '__main__':
  path = sys.argv[1]
  files = [os.path.join(path, file) for file in os.listdir(path)]
  media = []
  Scan(path[1:], files, media, [])
  print "SS: media |", media, "|"
