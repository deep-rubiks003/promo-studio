"""Read-only projection of checkpointed output; never promotes or regenerates drafts."""
import copy
from parser import validate_cut

def view(project):
    p=copy.deepcopy(project);pending=p.get('pending_generation')
    if not pending or not pending.get('groups'):
        p['saved_output_view']=True
        groups=p.get('groups',[])
    else:
        groups=copy.deepcopy(pending['groups']);p['groups']=groups;p['brief']=copy.deepcopy(pending['brief']);p['proposals']={}
    brief=p.get('brief') or {};reviewed=eligible=drafts=0
    p['proposals']={}
    for group in groups:
        for draft in group.get('variations',[]):
            drafts+=1
            done=draft.get('review_status')=='reviewed' or bool(draft.get('narrative') and draft.get('rubric'))
            if done:reviewed+=1
            else:
                draft['narrative']={};draft['rubric']=[];draft['review_status']='pending'
                draft['review_notes']='Draft saved; editorial review has not completed. No score or approval is available.'
            draft['validation']=validate_cut(draft,p['episodes'],group.get('source_ids',brief.get('selected_ids',[])),brief.get('minimum',20),brief.get('maximum',60),brief.get('structure','continuous'))
            eligible+=int(draft['validation']['eligible'])
    requested=len(brief.get('episode_groups',[])) or brief.get('group_count',len(groups))
    p.update(saved_output_view=True,saved_output_summary=dict(saved=drafts,reviewed=reviewed,eligible=eligible,needs_changes=drafts-eligible,awaiting_review=drafts-reviewed,not_generated=max(0,requested-len(groups)),requested=requested))
    return p
