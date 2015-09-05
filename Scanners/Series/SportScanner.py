#
# Most code here is copyright (c) 2010 Plex Development Team. All rights reserved.
#

import re, os, os.path, random
import Media, VideoFiles, Stack, Utils
from mp4file import mp4file, atomsearch

sports_regexps = [
    '(?P<show>.*?)[^0-9a-zA-Z]+(?P<year>[0-9]{4})[^0-9a-zA-Z]+(?P<month>[0-9]{2})[^0-9a-zA-Z]+(?P<day>[0-9]{2})[^0-9a-zA-Z]+(?P<title>.*)\.[^\.]+?$',
  ]

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
          year = int(match.group('year'))
          month = int(match.group('month'))
          day = int(match.group('day'))
          show = match.group('show')
          show = re.sub( '[^0-9a-zA-Z]+', ' ', show)
          title = match.group('title')
          title = re.sub( '[^0-9a-zA-Z]+', ' ', title)
          ep = int('%02d%02d%04d' % (month, day, (random.randint(0,9999))))
          # Use the year as the season
          tv_show = Media.Episode(show, year, ep, title, year)
          tv_show.released_at = '%d-%02d-%02d' % (year, month, day)
          tv_show.parts.append(i)
          mediaList.append(tv_show)

          done = True
          break

      if done == False:
        print "SS: Trying to match based off directory"

      if done == False:
        print "SS: Trying to match multipart"

      if done == False:
        print "Got nothing for:", file
          
  # Stack the results.
  Stack.Scan(path, files, mediaList, subdirs)

import sys
    
if __name__ == '__main__':
  path = sys.argv[1]
  files = [os.path.join(path, file) for file in os.listdir(path)]
  media = []
  Scan(path[1:], files, media, [])
  print "SS: media |", media, "|"
