[CmdletBinding()]
param (
    #[Parameter(Mandatory = $true)]
    [datetime]
    $StartDate = "2015-10-10",

    [datetime]
    $EndDate = (Get-Date).AddDays(-1).Date
)

#Get all the teams in the league according to thesportsdb
$thesportsdbTeams = @{}

$teamUrl = "http://www.thesportsdb.com/api/v1/json/8123456712556/lookup_all_teams.php?id=4380"
$teams = (Invoke-RestMethod $teamUrl).teams

#Make a hash table that we can use here to map the results of our original search to thesportsdb values
foreach ( $team in $teams ) {
    try {
        $thesportsdbTeams.Add($team.strTeam, $team)
    } catch {
        Write-Error "No short team name for $($team.strTeam)"
    }
}

$events = @()
$totalGames = 0

# Get all games through this time period
$startDateStr = "$($StartDate.Year)-$($StartDate.Month.ToString("00"))-$($StartDate.Day.ToString("00"))"
$endDateStr = "$($EndDate.Year)-$($EndDate.Month.ToString("00"))-$($EndDate.Day.ToString("00"))"
$schedurl = "https://statsapi.web.nhl.com/api/v1/schedule?startDate=$startDateStr&endDate=$endDateStr"
$dates = (Invoke-RestMethod $schedUrl).dates

#Get all the events that happened on this day
foreach ($date in $dates) {
    foreach ($game in $date.games) {
        $homeTeamLong = ($game.teams.home.team.name -replace 'é', 'e')
        $awayTeamLong = ($game.teams.away.team.name -replace 'é', 'e')
        #Create an object for this event
        $events += New-Object -TypeName psobject -Property @{
            idEvent = $null
            idSoccerXML = $null
            strEvent = "$homeTeamLong vs $awayTeamLong"
            strFilename = "NHL $($date.date) $homeTeamLong vs $awayTeamLong"
            strSport = "Ice Hockey"
            idLeague = "4380"
            strLeague = "NHL"
            strSeason = "1718"
            strDescriptionEN = $null
            strHomeTeam = $homeTeamLong
            strAwayTeam = $awayTeamLong
            intHomeScore = "$($game.teams.home.score)"
            intRound = "0"
            intAwayScore = "$($game.teams.away.score)"
            intSpectators = $null
            strHomeGoalDetails = $null
            strHomeRedCards = $null
            strHomeYellowCards = $null
            strHomeLineupGoalkeeper = $null
            strHomeLineupDefense = $null
            strHomeLineupMidfield = $null
            strHomeLineupForward = $null
            strHomeLineupSubstitutes = $null
            strHomeFormation = $null
            strAwayRedCards = $null
            strAwayYellowCards = $null
            strAwayGoalDetails = $null
            strAwayLineupGoalkeeper = $null
            strAwayLineupDefense = $null
            strAwayLineupMidfield = $null
            strAwayLineupForward = $null
            strAwayLineupSubstitutes = $null
            strAwayFormation = $null
            intHomeShots = $null
            intAwayShots = $null
            dateEvent = "$properDate"
            strDate = $null
            strTime = $null
            strTVStation = $null
            idHomeTeam = $($thesportsdbTeams.($game.teams.home.team.name -replace 'é', 'e').idTeam)
            idAwayTeam = $($thesportsdbTeams.($game.teams.away.team.name -replace 'é', 'e').idTeam)
            strResult = $null
            strRaceCircuit = $null
            strRaceCountry = $null
            strRaceLocality = $null
            strPoster = $null
            strFanart = $null
            strThumb = $null
            strBanner = $null
            strMap = $null
            strLocked = "unlocked"
        }
    }
} 

$events | ConvertTo-Csv -NoTypeInformation | out-file -Encoding ascii -FilePath "C:\temp\NHLresults.csv" -Force


