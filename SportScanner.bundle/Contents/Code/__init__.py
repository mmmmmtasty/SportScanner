import re, time, os, datetime
from pprint import pprint
from difflib import SequenceMatcher

netLock = Thread.Lock()

# Keep track of success/failures in a row.
successCount = 0
failureCount = 0

MIN_RETRY_TIMEOUT = 2
RETRY_TIMEOUT = MIN_RETRY_TIMEOUT
TOTAL_TRIES   = 1
BACKUP_TRIES  = -1

SPORTSDB_ROOT = "http://www.thesportsdb.com/api/v1/json/1/"

headers = {'User-agent': 'Plex/Nine'}

def similar(a, b):
    return SequenceMatcher(None, a, b).ratio()

def GetResultFromNetwork(url, fetchContent=True):
  global successCount, failureCount, RETRY_TIMEOUT

  try:
    netLock.acquire()
    Log("SS: Retrieving URL: " + url)

    tries = TOTAL_TRIES
    while tries > 0:

      try:
        result = HTTP.Request(url, headers=headers, timeout=60)
        if fetchContent:
          result = result.content
        
        failureCount = 0
        successCount += 1
          
        if successCount > 20:
          RETRY_TIMEOUT = max(MIN_RETRY_TIMEOUT, RETRY_TIMEOUT/2)
          successCount = 0
        
        # DONE!
        return result
        
      except Exception, e:
        
        # Fast fail a not found.
        if e.code == 404:
          return None
        
        failureCount += 1
        Log("Failure (%d in a row)" % failureCount)
        successCount = 0
        time.sleep(RETRY_TIMEOUT)
      
        if failureCount > 5:
          RETRY_TIMEOUT = min(10, RETRY_TIMEOUT * 1.5)
          failureCount = 0
  
  finally:
    netLock.release()
    
  return None

def Start():
  HTTP.CacheTime = CACHE_1HOUR * 24
  
class SportScannerAgent(Agent.TV_Shows):
  name = 'SportScanner'
  languages = ['en']

  def search(self, results, media, lang, manual=False):
    #Get the show name and query the web service for that info
    Log("SS: %s" % media.show)
    if re.search('EPL',media.show,re.IGNORECASE):
      id = 24
      Log("SS: Matched EPL")
      filename = "R:\\scripts\\SportScanner\\Metadata\\{0}.json".format(id)
      size = os.path.getsize(filename)
      fd = os.open(filename, os.O_RDONLY)
      file_input = os.read(fd, size)
      data = JSON.ObjectFromString(file_input)
      Data.Save( "{0}.json".format(id), file_input)

      Log("SS: Name: %s" % data['name'])
      Log("SS: id: %d" % int(data['id']))
      Log("SS: year: %d" % int(data['year']))
      results.Append(
        MetadataSearchResult(
          id    = data['id'],
          name  = data['name'],
          year  = int(data['year']),
          lang  = 'en',
          score = int(95)
        )
      )

#Update is passed A WHOLE SHOW and has to work out what episodes it has and therefore what it should be updating
  def update(self, metadata, media, lang):
    Log("SS: update for: {0}".format(metadata.id))

    #Get the zip archive for the show
    try:
      file_input = Data.Load("{0}.json".format(metadata.id))
    except:
      #We need to go and get it from the web then
      pass
    data = JSON.ObjectFromString(file_input)

    #Fill in any missing information for show and download posters/banners
    metadata.title = data['name']
    metadata.summary = data['summary']
    metadata.genres = data['sport']
    try:
     metadata.originally_available_at = data['originally_available_at']
    except:
     pass

    #Work out what episodes we have and match them to ones in the right season
    @parallelize
    def UpdateEpisodes():

      #Go through available episodes
      for s in media.seasons:
        # I don't believe either of these actually do anything
        metadata.seasons[s].summary = data['seasons'][s]['summary']
        metadata.seasons[s].title = data['seasons'][s]['name']

        Log("SS: Downloading posters for {0}".format(data['seasons'][s]['name']))
        # Download the episode thumbnail
        valid_names = list()

        if 'posters' in data['seasons'][s]:
          for poster in data['seasons'][s]['posters']:
            try:
              metadata.seasons[s].posters[poster] = Proxy.Media(GetResultFromNetwork(poster, False))
              valid_names.append(poster)
            except:
              Log("SS: Failed to add poster for {0}".format(s))
              pass
        else:
          Log("SS: No season posters to download for {0}".format(s))

        metadata.seasons[s].posters.validate_keys(valid_names)

        for e in media.seasons[s].episodes:
          episode = metadata.seasons[s].episodes[e]
          season_metadata = data['seasons'][s]
          episode_media = media.seasons[s].episodes[e]
          try:
            Log("SS: Matching episode number %s: %s" % e, episode.title)
          except:
            pass
          #Matching episodes is not so trivial - the episode number is meaningless. Matching must happen via date and title
          @task
          def UpdateEpisode(episode=episode,season_metadata=season_metadata,episode_media=episode_media):
            c = None
            best_score = 0
            #First try and match an episode
            Log("SS: Looking for match for %s" % episode_media.title)
            for p in season_metadata['episodes']:
              #Log("SS: Comparing {0} to {1}".format(season_metadata['episodes'][p]['title'], episode_media.title))
              if season_metadata['episodes'][p]['date'] and episode_media.originally_available_at:
                if season_metadata['episodes'][p]['date'] == episode_media.originally_available_at:
                  closeness = similar(season_metadata['episodes'][p]['title'], episode_media.title)
                  Log("SS: Match ratio of {0} between {1} and {2}".format(1, episode_media.title, season_metadata['episodes'][p]['title']) )
                  #If they are a perfect match then we are done
                  if closeness == 1:
                    best_score = 1
                    c = p
                    break
                  elif closeness > best_score:
                    best_score = closeness
                    c = p
                    continue

            #Only accept if the match is better than 80%
            if best_score > 0.8 and c:
              Log("SS: Updating metadata for {0}".format(season_metadata['episodes'][c]['title']))
              episode.title = season_metadata['episodes'][c]['title']
              episode.summary = season_metadata['episodes'][c]['summary']
              episode.originally_available_at = datetime.datetime.strptime(season_metadata['episodes'][c]['date'], "%Y-%m-%d").date()
            else:
              try:
                Log("SS: Best match was %d" % best_score)
              except:
                pass
              return

            Log("SS: Downloading thumbnail for {0}".format(episode.title))
            # Download the episode thumbnail
            valid_names = list()
            if 'thumbs' in season_metadata['episodes'][c]:
              for thumb in season_metadata['episodes'][c]['thumbs']:
                try:
                  episode.thumbs[thumb] = Proxy.Media(GetResultFromNetwork(thumb, False))
                  valid_names.append(thumb)
                except:
                  Log("SS: Failed to add poster for {0}".format(episode.title))
                  pass
            else:
              Log("SS: No season posters to download for {0}".format(episode.title))

            episode.thumbs.validate_keys(valid_names)

    # Maintain a list of valid image names
    valid_names = list()

    @parallelize
    def DownloadImages():
      Log("Downloading Images")
      # Add a download task for each image
      i = 0
      for banner_url in data['banners']:
        i += 1
        @task
        def DownloadImage(metadata=metadata, banner_url=banner_url, i=i, valid_names=valid_names):

          valid_names.append(banner_url)

          if banner_url not in metadata.banners:
            Log("SS: Downloading banner {0}".format(banner_url))
            try:
              metadata.banners[banner_url] = Proxy.Preview(GetResultFromNetwork(banner_url, False), sort_order=i)
            except:
              Log("SS: Failed to set banner for {0}".format(metadata.title))
              pass

      # Add a download task for each image
      for poster_url in data['posters']:
        @task
        def DownloadImage(metadata=metadata, poster_url=poster_url, i=i, valid_names=valid_names):

          valid_names.append(poster_url)

          if poster_url not in metadata.posters:
            Log("SS: Downloading poster {0}".format(poster_url))
            try:
              metadata.posters[poster_url] = Proxy.Preview(GetResultFromNetwork(poster_url, False), sort_order=i)
            except:
              Log("SS: Failed to set poster for {0}".format(metadata.title))
              pass

      # Check each poster, background & banner image we currently have saved. If any of the names are no longer valid, remove the image
      metadata.posters.validate_keys(valid_names)
      metadata.art.validate_keys(valid_names)
      metadata.banners.validate_keys(valid_names)
