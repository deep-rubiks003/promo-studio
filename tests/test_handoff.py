from test_new_workflow import studio

def test_handoff_survives_selective_rollback(studio):
    p,c,_=studio;ep=p['episodes'][0]
    v=dict(id='draft',angle='<script>unsafe</script>',reason='Clear conflict',segments=[dict(episode_id=ep['id'],start=0,end=25)],beats=[dict(role=r,event_ids=[ep['events'][i]['id']],reason='Source') for r,i in [('Hook',0),('Context',1),('Escalation',1),('Cliffhanger',2)]],question='What happens next?',narrative=dict(standalone=True,escalation=True,cliffhanger=True,coherent=True),rubric=[])
    import server
    p.update(brief=dict(selected_ids=[ep['id']],minimum=20,maximum=60,structure='continuous'),groups=[dict(id='group',title='Letter',source_ids=[ep['id']],variations=[v])]);server.save(p)
    r=c.get('/api/projects/'+p['id']+'/handoff')
    assert r.status_code==200
    assert 'Ordered source cuts and original dialogue' in r.text
    assert 'Who took my letter?' in r.text and 'Unanswered question' in r.text
    assert 'Not checked.' in r.text and 'Clear conflict' in r.text
    assert '<script>unsafe' not in r.text and '&lt;script&gt;' in r.text
    assert 'attachment' in r.headers['content-disposition']
    assert not any(route.path.endswith('/undo') for route in server.app.routes)
