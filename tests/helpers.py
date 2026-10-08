def player(internal_id, object_id=None, name=None):
    return {'id': internal_id, 'objectId': object_id if object_id is not None else internal_id+1000,
            'name': name or f'Player {internal_id}'}


def details():
    a = [{'player': player(i)} for i in range(1, 12)]
    b = [{'player': player(i)} for i in range(12, 23)]
    return {'match': {'generalClassId':6,'internationalClassId':None,'format':'T20','series':{'name':'Indian Premier League'},
                      'startDate':'2023-04-01','teams':[{'team':{'id':10,'name':'A'}},{'team':{'id':20,'name':'B'}}],
                      'ground':{'name':'Ground'},'season':'2023'},
            'content': {'matchPlayers':{'teamPlayers':[
                {'type':'PLAYING','team':{'id':10,'name':'A'},'players':a},
                {'type':'PLAYING','team':{'id':20,'name':'B'},'players':b}]},
                'innings':[{'inningNumber':1,'team':{'id':10,'name':'A'},
                            'inningBatsmen':[{'player':player(1),'runs':0,'balls':1}],
                            'inningBowlers':[{'player':player(12),'balls':1}],
                            'inningDidNotBats':[]} ]}}


def ball(batter=1, bowler=12, non_striker=2):
    return {'id':100,'inningNumber':1,'overNumber':1,'oversActual':0.1,'oversUnique':0.1,
            'batsmanPlayerId':batter,'bowlerPlayerId':bowler,'nonStrikerPlayerId':non_striker,
            'batsmanRuns':0,'totalRuns':0,'totalInningRuns':0}
