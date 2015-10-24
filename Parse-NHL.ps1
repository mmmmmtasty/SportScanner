Param(
    #[Parameter(Mandatory = $true)]
    [datetime]$StartDate = "2015-10-10",
    [datetime]$EndDate = (Get-Date).AddDays(-1).Date
)

#Get all the teams in the league according to thesportsdb
$thesportsdbTeams = @{}

$teamUrl = "http://www.thesportsdb.com/api/v1/json/8123456712556/lookup_all_teams.php?id=4380"
$teams = (Invoke-WebRequest $teamUrl | ConvertFrom-Json).teams

#Make a hash table that we can use here to map the results of our original search to thesportsdb values
foreach ( $team in $teams ) {
    try {
        if ( $team.strTeam -eq "Buffalo Sabres" ) {
            Write-Host "Setting Buffalo Sabres short name to BUF"
            $team.strTeamShort = "BUF"
        } elseif ( $team.strTeamShort -eq "PHX" ) {
            Write-Host "Setting PHX short name to ARI"
            $team.strTeamShort = "ARI"
        }
        $thesportsdbTeams.Add( $team.strTeamShort, $team )
    } catch {
        Write-Error "No short team name for $($team.strTeam)"
    }
}

#Work out all the dates that we need to query for
$dates = @($StartDate.Date)
while ( $StartDate -ne $EndDate -and $EndDate ) {
    $StartDate = $StartDate.AddDays(1)
    $dates += $StartDate.Date
}

$events = @()
$totalGames = 0

#Get all the events that happened on this day
foreach ( $date in $dates) {
    $schedUrl = "http://live.nhle.com/GameData/GCScoreboard/$($date.Year)-$($date.Month)-$($date.Day).jsonp" 
    $games = ([System.Text.Encoding]::ASCII.GetString((Invoke-WebRequest $schedUrl).Content) -replace "loadScoreboard\((.*)\)", '$1' | ConvertFrom-Json).games
    if ( !$games ) {
        continue
    }
    foreach ($game in $games) {
        $totalGames++
        #Check we have all the attr we need - hta, ata
        #Create an object for this event
        $events += New-Object -TypeName psobject -Property @{
            idEvent = $null
            idSoccerXML = $null
            strEvent = "$($thesportsdbTeams.($game.hta).strTeam) vs $($thesportsdbTeams.($game.ata).strTeam)"
            strFilename = "NHL $($date.Year)-$($date.Month)-$($date.Day) $($thesportsdbTeams.($game.hta).strTeam) vs $($thesportsdbTeams.($game.ata).strTeam)"
            strSport = "Ice Hockey"
            idLeague = "4380"
            strLeague = "NHL"
            strSeason = "1516"
            strDescriptionEN = $null
            strHomeTeam = "$($thesportsdbTeams.($game.hta).strTeam)"
            strAwayTeam = "$($thesportsdbTeams.($game.ata).strTeam)"
            intHomeScore = "$($game.hts)"
            intRound = "0"
            intAwayScore = "$($game.ats)"
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
            intHomeShots = "$($game.htsog)"
            intAwayShots = "$($game.atsog)"
            dateEvent = "$($date.Year)-$($date.Month)-$($date.Day)"
            strDate = $null
            strTime = $null
            strTVStation = $null
            idHomeTeam = $($thesportsdbTeams.($game.hta).idTeam)
            idAwayTeam = $($thesportsdbTeams.($game.ata).idTeam)
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

$events | ConvertTo-Csv -NoTypeInformation | out-file -Encoding ascii -FilePath "C:\temp\results.csv" -Force


