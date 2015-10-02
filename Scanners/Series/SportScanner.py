#
# Most code here is copyright (c) 2010 Plex Development Team. All rights reserved.
#

import re, os, os.path, random, urllib2
import Media, VideoFiles, Stack, Utils
from pprint import pprint

regex_all_in_file_name = [
    '(?P<show>.*?)[^0-9a-zA-Z]+(?P<year>[0-9]{4})[^0-9a-zA-Z]+(?P<month>[0-9]{2})[^0-9a-zA-Z]+(?P<day>[0-9]{2})[^0-9a-zA-Z]+(?P<title>.*)$',
  ]

regex_date_title_file_name = [
    '.*'
  ]

seasons = { "Soccer" : { "split_date" : [7,1], "season_format" : "xxyy"},
            "Ice Hockey" : { "split_date" : [7,1], "season_format" : "xxyy"},
            "Motorsport" : { "season_format" : "yyyy"},
            "Baseball" : { "season_format" : "yyyy"},
			"Basketball" : { "split_date" : [7,1], "season_format" : "xxyy"}}
            
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
  # No files here? Then what are we doing!
  clean_files = dict()
  if len(files) == 0:
    return
  else:
    for i in files:
      file = os.path.basename(i)
      (file, ext) = os.path.splitext(file)
      # Minor cleaning on the file to avoid false matches on H.264, 720p, etc.
      whackRx = ['([hHx][\.]?264)[^0-9].*', '[^[0-9](720[pP]).*', '[^[0-9](1080[pP]).*', '[^[0-9](480[pP]).*', '[^[0-9](540[pP]).*']
      for rx in whackRx:
        file = re.sub(rx, "", file)
      clean_files[file] = i

  paths = Utils.SplitPath(path)
  
  if len(paths) == 1 and len(paths[0]) == 0:
    print "SS: In TLD, no files here can be scanned"
    return
  elif len(paths) == 1 and len(paths[0]) > 0:
    # The first layer or directories has to be the sport or nothing will work
    # Any files we find at this level MUST have the sport information in their filename
    sport = paths[0]
    print "SS: Assuming {0} is a sport, scanning individual files".format(sport)
    pprint(clean_files)
    # Look for ALL the information we need in the filename
    for file in clean_files:
      print "SS: Working on file | {0} |".format(file)
      for rx in regex_all_in_file_name:
        match = re.search(rx, file, re.IGNORECASE)
        if match:
          print "SS: matched regex | {0} |".format(rx)
          year = match.group('year')
          month = int(match.group('month'))
          day = int(match.group('day'))
          show = re.sub( '[^0-9a-zA-Z]+', ' ', match.group('show'))
          show = "{0}: {1}".format(sport, show)
          title = re.sub( '[^0-9a-zA-Z]+', ' ', match.group('title'))
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

          ep = int('%02d%02d%04d' % (month, day, (random.randint(0,9999))))
          tv_show = Media.Episode(show, season, ep, title, int(year))
          tv_show.released_at = '%s-%02d-%02d' % (year, month, day)
          tv_show.parts.append(clean_files[file])
          mediaList.append(tv_show)
          break
        else:
          print "SS: No match found for {0}".format(file)
  elif len(paths) == 2:
    # Here we assume that the TLD is the sport and the next level is the show name
    sport = paths[0]
    show = paths[1]
    print "SS: Assuming {0} is a sport and {1} is the show, scanning individual files".format(sport, show)
    pprint(clean_files)
    # Look for ALL the information we need in the filename
    for file in clean_files:
      print "SS: Working on file | {0} |".format(file)
      for rx in regex_date_title_file_name:
        match = re.search(rx, file, re.IGNORECASE)
        if match:
          print "SS: matched regex | {0} |".format(rx)
          year = match.group('year')
          month = int(match.group('month'))
          day = int(match.group('day'))
          title = re.sub( '[^0-9a-zA-Z]+', ' ', match.group('title'))
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

          ep = int('%02d%02d%04d' % (month, day, (random.randint(0, 9999))))
          tv_show = Media.Episode(show, season, ep, title, int(year))
          tv_show.released_at = '%s-%02d-%02d' % (year, month, day)
          tv_show.parts.append(clean_files[files])
          mediaList.append(tv_show)
          break


  # Stack the results.
  Stack.Scan(path, files, mediaList, subdirs)

import sys
    
if __name__ == '__main__':
  path = sys.argv[1]
  files = [os.path.join(path, file) for file in os.listdir(path)]
  media = []
  Scan(path[1:], files, media, [])
  print "SS: media |", media, "|"
