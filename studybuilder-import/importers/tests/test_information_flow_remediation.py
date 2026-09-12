import copy
import pytest
from .test_proposal_v2_native_operations import candidate, proposal_object, envelope, receipt, mark_create_request
from ..mappings.proposal_v2_native_operations import native_operation_plan, _activity_instruction_operation_values
from ..mappings.proposal_v2_capture_operations import capture_operation_values
from ..run_import_osb_proposal_v2 import ImportOsbProposalV2
from ..run_import_360i import Import360i
from ..mappings import payload_to_osb

def test_reviewed_capture_create_is_current_native_operation_with_all_fields_read_back():
    native = {"name":"Real capture", "library_name":"Sponsor", "datatype":"integer", "length":3,
              "translated_texts":[{"language":"en", "text":"Full source instruction", "text_type":"Description"}]}
    offered=candidate("candidate", "OdmItem", "OdmItem_1")
    item=proposal_object("capture", "capture", "OdmItem", offered)
    item['source']={'values':[{'name':'nativeBody','sourcePath':'/nativeBody','value':native}]}
    review=receipt([item],[offered]);mark_create_request(review,'capture')
    plan=native_operation_plan(envelope([item]),review,'Study_1','DRAFT')
    assert not plan['blockers']
    operation=plan['operations'][0]
    assert operation['path']=='/odms/items'
    assert operation['body']==native
    assert operation['read_after_write']['match']==native
    assert ImportOsbProposalV2._native_uid(operation,{'uid':'actual-uid'})=='actual-uid'

@pytest.mark.parametrize('patch',[{'arbitrary':True},{'length':None}])
def test_unstated_text_length_or_unknown_capture_property_never_defaults(patch):
    native={'name':'Text','library_name':'Sponsor','datatype':'text','translated_texts':[{'text':'Source'}],**patch}
    assert capture_operation_values('OdmItem',{'nativeBody':native})[-1]

def test_template_same_name_alone_cannot_consume_different_instruction():
    item={'source':{'values':[{'name':'triggerCondition','value':'Discontinue intervention if stated trigger occurs'}]}}
    assert _activity_instruction_operation_values(item,{'resourceType':'ActivityInstructionTemplate','uid':'native','name':'Collect adverse events','parameterCount':0})[-1]=='OSB_NATIVE_V2_INSTRUCTION_RENDERED_CONTENT_MISMATCH'

def test_native_purpose_identity_preserves_operators_punctuation_case_and_bullets():
    value='PCR < 72 hours OR results > 24 hours; â€¢ Treatment A.'
    assert Import360i._purpose_plain(value)==value
    assert Import360i._purpose_plain('<p>PCR &lt; 72 hours OR results &gt; 24 hours; â€¢ Treatment A.</p>')==value
    assert Import360i._purpose_plain('Treatment A.')!=Import360i._purpose_plain('Treatment A')
    assert Import360i._purpose_plain('Treatment A')!=Import360i._purpose_plain('treatment A')

def test_comparison_mapper_never_invents_dates_epochs_lengths_or_scalar_criteria():
    plans=payload_to_osb.visit_plan({'visits':[{'refKey':'missing','name':'Follow-up','type':'scheduled'}]}, {})
    assert plans[0]['stop']=='OSB_VISIT_TIMING_AUTHORITY_REQUIRED'
    diff=payload_to_osb.visit_diff({'visits':[{'refKey':'missing','name':'Follow-up','type':'scheduled'}]}, {'missing':{'uid':'kept'}})
    assert diff['stop'] and not diff['delete']
    assert payload_to_osb.epochs_plan({'epochs':[]}) == ([],False)
    assert 'Treatment' not in payload_to_osb.epoch_subtype_candidates('Unspecified phase')
    with pytest.raises(ValueError,match='NONSCALAR'):
        payload_to_osb.odm_item_body({'name':'Eligibility','refKey':'criteria','datatype':'text','datatypeHint':'criteria_list','length':200},{},{})
    with pytest.raises(ValueError,match='PRECISION_PAIR'):
        payload_to_osb.odm_item_body({'name':'Result','refKey':'result','datatype':'float','length':5},{},{})
    assert payload_to_osb.odm_item_body({'name':'Result','refKey':'result','datatype':'float','length':5,'significantDigits':2},{},{})['length']==5

def test_native_visit_anchor_excludes_held_rows_and_fractional_timing_never_rounds():
    rows=[{'refKey':'held','name':'Unscheduled','type':' unscheduled ','scheduleDay':0},
          {'refKey':'timed','name':'Day10','type':'scheduled','visitTypeName':'Treatment','scheduleDay':10}]
    plans=payload_to_osb.visit_plan({'visits':rows},{'held':'epoch','timed':'epoch'})
    assert plans[0]['stop']=='OSB_UNSCHEDULED_OCCURRENCE_BINDING_REQUIRED'
    assert plans[1]['is_global_anchor_visit'] is True and plans[1]['time_value']==0
    fractional=payload_to_osb.visit_plan({'visits':[{'refKey':'fraction','name':'Half day','type':'scheduled','scheduleDay':1.5}]},{})
    assert fractional[0]['stop']=='OSB_VISIT_FRACTIONAL_OR_INVALID_DAY_UNSUPPORTED'

def test_native_readback_tests_nested_supplied_properties_and_json_boolean_type():
    wanted={'translated_texts':[{'text':'Full instruction','language':'en'}], 'required':True}
    actual={'uid':'native','translated_texts':[{'text':'Full instruction','language':'en','uid':'translation'}],'required':True}
    assert ImportOsbProposalV2._matching_records([actual],wanted)==[actual]
    altered=copy.deepcopy(actual);altered['translated_texts'][0]['text']='Shortened'
    assert not ImportOsbProposalV2._matching_records([altered],wanted)
    assert not ImportOsbProposalV2._matching_records([{'uid':'native'}],{'comment':None})
    assert ImportOsbProposalV2._matching_records([{'uid':'native','comment':None}],{'comment':None})
    altered=copy.deepcopy(actual);altered['required']=1
    assert not ImportOsbProposalV2._matching_records([altered],wanted)

def test_complete_reference_collection_is_not_a_paged_envelope_or_prefix_match():
    wanted={'uid':'group','items':[{'uid':'b','order_number':2},{'uid':'a','order_number':1}]}
    actual={'uid':'group','items':[{'uid':'a','order_number':1,'name':'A'},{'uid':'b','order_number':2,'name':'B'}]}
    assert ImportOsbProposalV2._matching_records(actual,wanted,collection=False)==[actual]
    actual['items'].append({'uid':'extra','order_number':3})
    assert not ImportOsbProposalV2._matching_records(actual,wanted,collection=False)

def test_native_attachment_sets_match_by_identity_and_still_verify_order_and_expression():
    wanted={'terms':[{'uid':'b','order':2},{'uid':'a','order':1}],
            'formal_expressions':[{'context':'Zulu','expression':'value > 1'},{'context':'Alpha','expression':'value < 9'}]}
    actual={key:list(reversed(value)) for key,value in wanted.items()}
    assert ImportOsbProposalV2._matching_records([actual],wanted)
    actual['terms'][0]['order']=99
    assert not ImportOsbProposalV2._matching_records([actual],{'terms':[{'uid':'b','order':2},{'uid':'a','order':1}]})

def test_group_domain_request_uses_actual_native_response_dto():
    body={'name':'Group','library_name':'Sponsor','repeating':'No','translated_texts':[{'text':'Source','language':'en','text_type':'Description'}],
          'sdtm_domain_uids':['Domain2','Domain1']}
    _path,_body,expected,error=capture_operation_values('OdmItemGroup',{'nativeBody':body})
    assert error is None
    assert 'sdtm_domain_uids' not in expected
    assert expected['sdtm_domains']==[{'term_uid':'Domain2'},{'term_uid':'Domain1'}]
    native={**{key:value for key,value in expected.items() if key!='sdtm_domains'},'sdtm_domains':[{'term_uid':'Domain1','submission_value':'AE'},{'term_uid':'Domain2','submission_value':'VS'}]}
    assert ImportOsbProposalV2._matching_records([native],expected)

def test_binding_plan_refuses_omitted_replacement_fields_before_any_patch():
    offers=[candidate('odm','OdmItem','Item_1'),candidate('instance','ActivityInstance','AI_1'),candidate('class','ActivityItemClass','AIC_1'),candidate('binding','OdmItemActivityBinding','unused')]
    objects=[proposal_object(name,name,kind,offer) for name,kind,offer in zip(['odm','instance','class','binding'],['OdmItem','ActivityInstance','ActivityItemClass','OdmItemActivityBinding'],offers)]
    objects[-1]['dependencyTargetKeys']=['odm','instance','class']
    objects[-1]['source']={'values':[{'name':'nativeBody','value':{'change_description':'Explicit binding review','activity_instances':[{'activity_instance_uid':'AI_1','activity_item_class_uid':'AIC_1','order':1}]}}]}
    review=receipt(objects,offers);mark_create_request(review,'binding')
    plan=native_operation_plan(envelope(objects),review,'Study_1','DRAFT')
    assert any(row['code']=='OSB_NATIVE_V2_ODM_ACTIVITY_BINDING_COMPLETE_REPLACEMENT_REQUIRED' for row in plan['blockers'])
    assert not any(row['family']=='OdmItemActivityBinding' for row in plan['operations'])

def test_parent_child_plan_resolves_receipt_uids_and_verifies_full_collection():
    translated=[{'language':'en','text_type':'Description','text':'Synthetic source'}]
    offers=[candidate('group','OdmItemGroup','unused-group'),candidate('item','OdmItem','unused-item'),candidate('link','OdmItemGroupItemLink','unused-link')]
    objects=[proposal_object(name,name,kind,offer) for name,kind,offer in zip(['group','item','link'],['OdmItemGroup','OdmItem','OdmItemGroupItemLink'],offers)]
    objects[0]['source']={'values':[{'name':'nativeBody','value':{'name':'Group','library_name':'Sponsor','repeating':'No','translated_texts':translated,'sdtm_domain_uids':[]}}]}
    objects[1]['source']={'values':[{'name':'nativeBody','value':{'name':'Item','library_name':'Sponsor','datatype':'integer','translated_texts':translated}}]}
    objects[2]['source']={'values':[{'name':'parentProposalObjectId','value':'group'},{'name':'children','value':[{'proposalObjectId':'item','relation':{'order_number':1,'mandatory':'No','vendor':{'attributes':[]}}}]}]}
    review=receipt(objects,offers)
    for name in ['group','item','link']:mark_create_request(review,name)
    plan=native_operation_plan(envelope(objects),review,'Study_1','DRAFT');assert not plan['blockers']
    link=next(op for op in plan['operations'] if op['family']=='OdmItemGroupItemLink')
    worker=object.__new__(ImportOsbProposalV2)
    resolved=worker._resolve_operation_references(link,[{'proposal_object_id':'group','family':'OdmItemGroup','native_uid':'NativeGroup'},{'proposal_object_id':'item','family':'OdmItem','native_uid':'NativeItem'}])
    assert resolved['path']=='/odms/item-groups/NativeGroup/items'
    assert resolved['body'][0]['uid']=='NativeItem'
    expected=resolved['read_after_write']['match']
    assert expected['uid']=='NativeGroup' and expected['items'][0]['uid']=='NativeItem'
    native={'uid':'NativeGroup','items':[dict(resolved['body'][0],name='Native metadata')]}
    assert worker._matching_records(native,expected,collection=False)
    native['items'].append({'uid':'unexpected'})
    assert not worker._matching_records(native,expected,collection=False)


def test_full_odm_import_holds_all_parent_replacements_and_stale_identity_propagation():
    from .test_360i_importer_reconciliation import _importer
    class NoCalls:
        def __getattr__(self, name):
            raise AssertionError(f"held child must prevent native request: {name}")
    worker = _importer(NoCalls())
    worker.same_payload_replay = False
    worker.ensure_vendor_namespace = lambda: {}
    worker.uid_map['items'] = {'I':'OldItem'}
    worker.uid_map['item_groups'] = {'G':'OldGroup'}
    worker.uid_map['forms'] = {'F':'OldForm'}
    worker.uid_map['study_events'] = {'V':'OldEvent'}
    before = copy.deepcopy(worker.uid_map)
    payload = {'source':{'studyId':'source','buildHash':'hash'},'sourceBundle':{},
      'odm':{'forms':[{'refKey':'F','name':'Form','itemGroups':[{'refKey':'G','name':'Group','orderNumber':1,
        'items':[{'refKey':'I','name':'Unbounded text','datatype':'text','orderNumber':1}]}]}]},
      'visits':[{'refKey':'V','name':'Visit'}], 'formVisitMatrix':[{'formRef':'F','visitRef':'V'}]}
    worker.ensure_odm(payload, 'Study_1', {}, {})
    assert worker.uid_map == before
    assert {row['kind'] for row in worker.census.stopped} == {'item','item_group','form','study_event'}
    assert len(worker.census.release_blockers) == 4


def test_visit_native_type_is_explicit_and_unstated_contact_is_omitted():
    from .test_360i_importer_reconciliation import _importer
    source={'refKey':'V','name':'Scheduled','type':'scheduled','scheduleDay':0}
    assert payload_to_osb.visit_plan({'visits':[source]}, {})[0]['stop']=='OSB_VISIT_TYPE_AUTHORITY_REQUIRED'
    source['visitTypeName']='Screening'
    plan=payload_to_osb.visit_plan({'visits':[source]}, {'V':'Epoch'})[0]
    worker=_importer(None);worker._lookup_ct_term=lambda _list,name: 'ScreenTerm' if name=='Screening' else None
    body,error=worker._visit_body(plan,'Study_1',{'V':'Epoch'},{'anchor_ref_uid':'Anchor','day_unit_uid':'Day'})
    assert error is None and body['visit_type']=={'term_uid':'ScreenTerm'}
    assert 'visit_contact_mode' not in body


def test_multiple_stated_epoch_bindings_hold_instead_of_last_wins():
    from .test_360i_importer_reconciliation import _importer, _EpochApi
    rows=[{'uid':'E1','epoch_name':'Screening','order':1,'description':'Screening'},
          {'uid':'E2','epoch_name':'Treatment','order':2,'description':'Treatment'}]
    worker=_importer(_EpochApi(rows));worker.uid_map['epochs']={}
    payload={'epochs':[{'name':'Screening','ordinal':1,'visitRefs':['V']}, {'name':'Treatment','ordinal':2,'visitRefs':['V']} ]}
    by_visit, _, _ = worker.ensure_epochs(payload,'Study_1')
    assert 'V' not in by_visit
    assert worker.census.stopped[0]['reason'].startswith('OSB_VISIT_EPOCH_BINDING_AMBIGUOUS:')


@pytest.mark.parametrize('tamper_readback', [False, True])
def test_purpose_reuses_same_run_exact_selection_and_retires_only_superseded_owned_identity(monkeypatch, tamper_readback):
    from .test_360i_importer_reconciliation import _importer
    class Api:
        posts=[]
        deleted=[]
        row=None
        def get_all_from_api(self,path,params=None):
            if path.endswith('/study-endpoints') and self.row:
                row=copy.deepcopy(self.row)
                if tamper_readback:row['endpoint']['name_plain']='Altered native text'
                return [row]
            return []
        def simple_post_to_api(self,path,body,*args,**kwargs):
            self.posts.append((path,body))
            assert len(self.posts)==1, 'second exact source statement must reuse the first successful selection'
            self.row={'study_endpoint_uid':'New','endpoint':{'name_plain':'Source endpoint.'},'endpoint_level':{'term_uid':'Secondary'},'study_objective':None,'timeframe':{'uid':'Time1','name_plain':'Day 29.'}}
            return copy.deepcopy(self.row)
        def simple_delete(self,path,*args):
            self.deleted.append(path);return True
    api=Api();worker=_importer(api)
    worker.uid_map['endpoints']={'a':'Old','external_source':'Keep'}
    # A removed prior source selection is owned; arbitrary native library entries are never enumerated for deletion.
    worker._purpose_existing=lambda *_args:([],{})
    worker._lookup_final_ct_term=lambda _list,name:({'term_uid':name},None)
    worker._ensure_purpose_template=lambda *_args,**_kwargs:'Template'
    worker._ensure_purpose_timeframe=lambda *_args:'Time1'
    endpoint={'text':'Source endpoint.','level_name':'Secondary','objective_ref':None,'unresolved_objective':{'statedRef':'Unresolved','reason':'No source objective authority'},'timeframe':'Day 29.'}
    plan={'objectives':[],'criteria':[],'blockers':[],'review_queue':[],'endpoints':[dict(endpoint,ref='a'),dict(endpoint,ref='b')]}
    monkeypatch.setattr(payload_to_osb,'study_purpose_plan',lambda _payload:plan)
    worker.ensure_study_purpose({},'Study_1')
    assert worker.uid_map['endpoints']=={'a':'New','b':'New'}
    assert sorted(api.deleted)==['/studies/Study_1/study-endpoints/Keep','/studies/Study_1/study-endpoints/Old']
    mismatch=[row for row in worker.census.stopped if row['kind']=='study_purpose_property_reconciliation']
    assert bool(mismatch) is tamper_readback


@pytest.mark.parametrize('field,value,list_name,uid,name', [('control_type_code','active comparator','Control Type','C49649','Active'),('intervention_model_code','parallel-group','Intervention Model','C82639','Parallel')])
def test_explicit_native_ct_lexical_aliases_still_require_unique_final_codelist_membership(field,value,list_name,uid,name):
    from .test_360i_importer_reconciliation import _importer
    class Api:
        def get_all_from_api(self,*_args,**_kwargs):
            return [{'term_uid':uid,'name':{'sponsor_preferred_name':name,'status':'Final'},'attributes':{'status':'Final'}}]
    worker=_importer(Api())
    found,error=worker._lookup_native_ct_term(list_name,field,value)
    assert error is None and found['term_uid']==uid
    assert worker._lookup_native_ct_term(list_name,field,'different unknown meaning')[0] is None


def test_literal_bracket_transport_keeps_display_and_does_not_introduce_template_parameters():
    import html,re
    source='Literal &amp; [keratoplasty] allowed; value < 5.'
    body=Import360i._purpose_html(source)
    assert re.findall(r"\[([\w\s\-]+)]",body)==[]
    assert html.unescape(re.sub(r'</?p>','',body))==source
