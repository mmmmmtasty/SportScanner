import re, time, os, datetime
from pprint import pprint
from difflib import SequenceMatcher

netLock = Thread.Lock()

# Keep track of success/failures in a row.
successCount = 0
failureCount = 0

MIN_RETRY_TIMEOUT = 2
RETRY_TIMEOUT = MIN_RETRY_TIMEOUT
TOTAL_TRIES = 1
BACKUP_TRIES = -1

SPORTSDB_API = "http://www.thesportsdb.com/api/v1/json/8123456712556/"

headers = {'User-agent': 'Plex/Nine'}


def similar(a, b):
    return round(SequenceMatcher(None, a, b).ratio(), 2)


def GetResultFromNetwork(url, fetchContent=True):
    global successCount, failureCount, RETRY_TIMEOUT

    url = url.replace(' ', '%20')

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
                    RETRY_TIMEOUT = max(MIN_RETRY_TIMEOUT, RETRY_TIMEOUT / 2)
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
    # Let's put this up when things are all stable shall we?
    HTTP.CacheTime = 0
    # HTTP.CacheTime = CACHE_1HOUR * 24


class SportScannerAgent(Agent.TV_Shows):
    name = 'SportScanner'
    languages = ['en']

    def search(self, results, media, lang, manual):
        # Get the show name and query the web service for that info
        match = re.match('^(.*?): (.*)', media.show)
        sport = match.group(1)
        show_title = match.group(2)
        Log("SS: Attempting to match {0}".format(show_title))
        url = "{0}search_all_leagues.php?s={1}".format(SPORTSDB_API, sport)
        try:
            potential_shows = (JSON.ObjectFromString(GetResultFromNetwork(url, True)))['countrys']
        except:
            Log("SS: Could not retrieve shows from thesportsdb.com")

        match = False

        # Check to see if there is a perfect match
        for i in range(0, len(potential_shows)):
            x = potential_shows[i]
            # Log("SS: Comparing {0] to {1}".format(x['strLeague'], show_title))
            if x['strLeague'] == show_title:
                Log("SS: Found a perfect match for {0}".format(show_title))
                results.Append(
                    MetadataSearchResult(
                        id=x['idLeague'],
                        name=show_title,
                        year=int(x['intFormedYear']),
                        lang='en',
                        score=100
                    )
                )
                match = True
                continue
            if x['strLeagueAlternate'] is not None:
                if show_title in x['strLeagueAlternate'].split(","):
                    results.Append(
                        MetadataSearchResult(
                            id=x['idLeague'],
                            name=show_title,
                            year=int(x['intFormedYear']),
                            lang='en',
                            score=100
                        )
                    )
                    match = True
                    continue

        # See if anything else comes close if we are doing a deeper manual search and haven't found anything already
        # if manual and not match:
        if not match and manual:
            Log("SS: Doing a comparison match, no exact matches found")
            for i in range(0, len(potential_shows)):
                x = potential_shows[i]
                current_league = x['strLeague']
                score = (similar(current_league, show_title) * 100)

                if score > 60:
                    Log("SS: Matched {0} with a score of {1}".format(current_league, score))
                    results.Append(
                        MetadataSearchResult(
                            id=x['idLeague'],
                            name=current_league,
                            year=int(x['intFormedYear']),
                            lang='en',
                            score=score
                        )
                    )
                if x['strLeagueAlternate'] is not None:
                    y = x['strLeagueAlternate'].split(",")
                    for j in range(0, len(y)):
                        score = (similar(y[j], show_title) * 100)
                        if score > 60:
                            results.Append(
                                MetadataSearchResult(
                                    id=x['idLeague'],
                                    name=show_title,
                                    year=int(x['intFormedYear']),
                                    lang='en',
                                    score=score
                                )
                            )

    def update(self, metadata, media, lang):
        Log("SS: update for: {0}".format(metadata.id))

        # We're not trying to read cached ones for now - let's get the new stuff every time
        try:
            url = "{0}lookupleague.php?id={1}".format(SPORTSDB_API, metadata.id)
            league_metadata = JSON.ObjectFromString(GetResultFromNetwork(url, True))['leagues'][0]
        except:
            pass

        # Fill in any missing information for show and download posters/banners
        metadata.title = league_metadata['strLeague']
        metadata.summary = league_metadata['strDescriptionEN']
        if not league_metadata['strSport'] in metadata.genres:
            metadata.genres.add(league_metadata['strSport'])
        try:
            metadata.originally_available_at = league_metadata['intFormedYear']
        except:
            pass

        # Work out what episodes we have and match them to ones in the right season
        @parallelize
        def UpdateEpisodes():
            # Go through available episodes
            for s in media.seasons:

                # Get events for the season we care about
                url = "{0}eventsseason.php?id={1}&s={2}".format(SPORTSDB_API, metadata.id, str(s).zfill(4))
                season_metadata = JSON.ObjectFromString(GetResultFromNetwork(url, True))

                # #There is currently no concept of a season having art in thesportsdb!
                #
                # # I don't believe either of these actually do anything
                # metadata.seasons[s].summary = data['seasons'][s]['summary']
                # metadata.seasons[s].title = data['seasons'][s]['name']
                #
                # Log("SS: Downloading posters for {0}".format(data['seasons'][s]['name']))
                # # Download the episode thumbnail
                # valid_names = list()
                #
                # if 'posters' in data['seasons'][s]:
                #   for poster in data['seasons'][s]['posters']:
                #     try:
                #       metadata.seasons[s].posters[poster] = Proxy.Media(GetResultFromNetwork(poster, False))
                #       valid_names.append(poster)
                #     except:
                #       Log("SS: Failed to add poster for {0}".format(s))
                #       pass
                # else:
                #   Log("SS: No season posters to download for {0}".format(s))
                #
                # metadata.seasons[s].posters.validate_keys(valid_names)

                for e in media.seasons[s].episodes:
                    episode = metadata.seasons[s].episodes[e]
                    episode_media = media.seasons[s].episodes[e]
                    try:
                        Log("SS: Matching episode number %s: %s" % e, episode.title)
                    except:
                        pass
                    # Matching episodes is not so trivial - the episode number is meaningless. Matching must happen
                    # via date and title
                    @task
                    def UpdateEpisode(episode=episode, season_metadata=season_metadata, episode_media=episode_media):
                        closest_event = None
                        best_score = 0
                        total_matches = 0
                        # First try and match an episode
                        Log("SS: Looking for match for %s" % episode_media.title)
                        for current_event in range(len(season_metadata['events'])):
                            # Log("SS: Comparing {0} to {1}".format(season_metadata['episodes'][p]['title'],
                            # episode_media.title))
                            if ("dateEvent" in season_metadata['events'][current_event]) and episode_media.originally_available_at:
                                if season_metadata['events'][current_event]['dateEvent'] == episode_media.originally_available_at:
                                    total_matches += 1
                                    closeness = similar(episode_media.title, season_metadata['events'][current_event]['strEvent'])
                                    Log("SS: Match ratio of {0} between {1} and {2}".format(closeness,
                                                                                            episode_media.title,
                                                                                            season_metadata['events'][
                                                                                                current_event]['strEvent']))
                                    # If they are a perfect match then we are done
                                    if closeness == 1:
                                        best_score = 1
                                        closest_event = current_event
                                        break
                                    elif closeness > best_score:
                                        best_score = closeness
                                        closest_event = current_event
                                        continue

                        Log("SS: Best match was {0}".format(best_score))
                        Log("SS: closest_event is {0}".format(closest_event))
                        # Only accept if the match is better than 80% or 50% if there is only one event on that date
                        if best_score > 0.8 or (best_score > 0.5 and total_matches == 1):
                            Log("SS: Updating metadata for {0}".format(season_metadata['events'][closest_event]['strEvent']))
                            episode.title = season_metadata['events'][closest_event]['strEvent']
                            episode.summary = "Matched by SportScanner"
                            episode.originally_available_at = datetime.datetime.strptime(
                                season_metadata['events'][closest_event]['dateEvent'], "%Y-%m-%d").date()

                            Log("SS: Downloading thumbnail for {0}".format(episode.title))
                            # Download the episode thumbnail
                            valid_names = list()
                            if season_metadata['events'][closest_event]['strThumb'] is not None:
                                thumb = season_metadata['events'][closest_event]['strThumb']
                                # thumb = "{0}/preview".format(season_metadata['events'][c]['strThumb'])
                                try:
                                    episode.thumbs[thumb] = Proxy.Media(GetResultFromNetwork(thumb, False))
                                    valid_names.append(thumb)
                                except:
                                    Log("SS: Failed to add thumbnail for {0}".format(episode.title))
                                    pass
                            else:
                                Log("SS: No thumbs to download for {0}".format(episode.title))

                        episode.thumbs.validate_keys(valid_names)

        @parallelize
        def DownloadImages():

            # Maintain a list of valid image names
            posters_to_dl = list()
            banners_to_dl = list()
            fanart_to_dl = list()

            Log("Downloading Images")
            # Each image is stored separately so we have to do something strange here
            if league_metadata['strPoster'] is not None:
                posters_to_dl.append(league_metadata['strPoster'])
                for b in range(1, 10):
                    key_name = "strPoster{0}".format(b)
                    if key_name in league_metadata:
                        if league_metadata[key_name] is not None:
                            posters_to_dl.append(league_metadata[key_name])
                            # posters_to_dl.append("{0}/preview".format(league_metadata[key_name]))
                    else:
                        break
                for i in range(len(posters_to_dl)):
                    poster_url = posters_to_dl[i]
                    Log("SS: Downloading {0}".format(poster_url))

                    @task
                    def DownloadImage(metadata=metadata, poster_url=poster_url, i=i):
                        if poster_url not in metadata.posters:
                            Log("SS: Downloading poster {0}".format(poster_url))
                            try:
                                metadata.posters[poster_url] = Proxy.Preview(GetResultFromNetwork(poster_url, False),
                                                                             sort_order=(i + 1))
                            except:
                                Log("SS: Failed to set poster for {0}".format(metadata.title))
                                pass
            else:
                Log("SS: No posters to download for {0}".format(league_metadata['strLeague']))

            metadata.posters.validate_keys(posters_to_dl)

            # Each image is stored separately so we have to do something strange here
            if league_metadata['strBanner'] is not None:
                banners_to_dl.append(league_metadata['strBanner'])
                for b in range(1, 10):
                    key_name = "strBanner{0}".format(b)
                    if key_name in league_metadata:
                        if league_metadata[key_name] is not None:
                            banners_to_dl.append(league_metadata[key_name])
                            # banners_to_dl.append("{0}/preview".format(league_metadata[key_name]))
                for i in range(len(banners_to_dl)):
                    banner_url = banners_to_dl[i]
                    Log("SS: Downloading {0}".format(banner_url))

                    @task
                    def DownloadImage(metadata=metadata, banner_url=banner_url, i=i):
                        if banner_url not in metadata.banners:
                            Log("SS: Downloading banner {0}".format(banner_url))
                            try:
                                metadata.banners[banner_url] = Proxy.Preview(GetResultFromNetwork(banner_url, False),
                                                                             sort_order=(i + 1))
                            except:
                                Log("SS: Failed to set banner for {0}".format(metadata.title))
                                pass
            else:
                Log("SS: No banners to download for {0}".format(league_metadata['strLeague']))

            metadata.banners.validate_keys(banners_to_dl)

            for b in range(1, 10):
                key_name = "strFanart{0}".format(b)
                if key_name in league_metadata:
                    if league_metadata[key_name] is not None:
                        fanart_to_dl.append(league_metadata[key_name])
                        # fanart_to_dl.append("{0}/preview".format(league_metadata[key_name]))
            for i in range(len(fanart_to_dl)):
                fanart_url = fanart_to_dl[i]
                Log("SS: Downloading {0}".format(fanart_url))

                @task
                def DownloadImage(metadata=metadata, fanart_url=fanart_url, i=i):
                    if fanart_url not in metadata.posters:
                        Log("SS: Downloading art {0}".format(fanart_url))
                        try:
                            metadata.art[fanart_url] = Proxy.Preview(GetResultFromNetwork(fanart_url, False),
                                                                     sort_order=(i + 1))
                        except:
                            Log("SS: Failed to set art for {0}".format(metadata.title))
                            pass

            metadata.art.validate_keys(fanart_to_dl)
