import json
from dataclasses import asdict
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cerebras_client import CerebrasClient, collect_delta, request_controls
from runner import objective_checks


def stream(deltas, finish='stop', done=True):
    packets = [{'choices': [{'delta': d, 'finish_reason': None}]} for d in deltas]
    packets.append({'choices': [{'delta': {}, 'finish_reason': finish}],
                    'usage': {'prompt_tokens': 20, 'completion_tokens': 12,
                              'completion_tokens_details': {'reasoning_tokens': 8}}})
    lines = ['data: ' + json.dumps(p) for p in packets]
    if done:
        lines.append('data: [DONE]')
    return httpx.Response(200, text='\n\n'.join(lines) + '\n\n')


class Bridge:
    def __init__(self):
        self.calls = []

    def request(self, operation, args):
        self.calls.append((operation, args))
        return {'isError': False, 'result': {'content': [{'type': 'text', 'text': 'applied'}]}}


class CerebrasTests(unittest.TestCase):
    def batch(self):
        return [
            {'index':0,'id':'face','function':{'name':'set_expression','arguments':'{"expression":"curious"}'}},
            {'index':1,'id':'sensor','function':{'name':'get_sensor_snapshot','arguments':'{}'}},
        ]

    def add_sensor(self, client):
        client._specs.append({'name':'get_sensor_snapshot','description':'Read sensors',
                              'inputSchema':{'type':'object','properties':{},'additionalProperties':False}})
        client._selected.add('get_sensor_snapshot')

    def client(self, replies, **options):
        self.requests = []
        def handle(request):
            self.requests.append(json.loads(request.content))
            reply = replies.pop(0)
            if isinstance(reply, Exception):
                raise reply
            return reply
        client = CerebrasClient(http_client=httpx.Client(transport=httpx.MockTransport(handle), base_url='https://test.invalid/v1'), system_prompt='MIST', **options)
        self.addCleanup(client.close)
        client._specs = [{'name': 'set_expression', 'description': 'Set face', 'inputSchema': {
            'type': 'object', 'properties': {'expression': {'type': 'string', 'enum': ['curious']}},
            'required': ['expression'], 'additionalProperties': False}}]
        client._selected = {'set_expression'}
        client._bridge = Bridge()
        client.new_session()
        return client

    def test_fragmented_tool_history_and_reasoning_is_never_emitted(self):
        client = self.client([
            stream([{'reasoning': 'PRIVATE'}, {'tool_calls': [{'index': 0, 'id': 'c1', 'function': {'name': 'set_', 'arguments': '{"exp'}}]},
                    {'tool_calls': [{'index': 0, 'function': {'name': 'expression', 'arguments': 'ression":"curious"}'}}]}], 'tool_calls'),
            stream([{'content': 'Done.'}]), stream([{'content': 'Still curious.'}])])
        events = []
        result = client.ask('Look curious', on_event=events.append)
        self.assertEqual(result.errors, [])
        self.assertEqual(result.text, 'Done.')
        self.assertEqual(len(client._bridge.calls), 1)
        self.assertEqual(client._bridge.calls[0][1]['arguments'], {'expression': 'curious'})
        self.assertNotIn('PRIVATE', json.dumps(events) + json.dumps(client.messages))
        self.assertEqual(result.timings['reasoning_output_tokens'], 16)
        client.ask('Which face?')
        self.assertEqual([m['role'] for m in self.requests[2]['messages']], ['system','user','assistant','tool','assistant','user'])
        self.assertEqual(self.requests[0]['reasoning_format'], 'hidden')
        self.assertFalse(self.requests[0]['parallel_tool_calls'])

    def test_bad_arguments_and_unlisted_tools_do_not_execute(self):
        for name, arguments in [('shell', '{}'), ('set_expression', '{bad'), ('set_expression', 'null'), ('set_expression', '{"expression":"evil"}')]:
            with self.subTest(name=name, arguments=arguments):
                client = self.client([stream([{'tool_calls': [{'index':0,'id':'c1','function':{'name':name,'arguments':arguments}}]}], 'tool_calls'), stream([{'content':'Could not apply it.'}])])
                result = client.ask('Try')
                self.assertEqual(client._bridge.calls, [])
                self.assertTrue(result.tool_calls[0].is_error)
                checks = objective_checks({'expression':'curious','allowed_expressions':['curious']}, result.text,
                    [{'name':name,'args':result.tool_calls[0].args,'is_error':True}], {}, {})
                self.assertTrue(any(c['passed'] is False for c in checks))

    def test_incomplete_tool_never_executes(self):
        for finish, done in [('length', True), ('tool_calls', False), ('stop', True)]:
            with self.subTest(finish=finish, done=done):
                client = self.client([stream([{'tool_calls':[{'index':0,'id':'c1','function':{'name':'set_expression','arguments':'{"expression":"curious"}'}}]}], finish, done)])
                result = client.ask('Try')
                self.assertTrue(result.errors)
                self.assertEqual(client._bridge.calls, [])

    def test_http_failure_has_no_retry_or_response_body_in_error(self):
        client = self.client([httpx.Response(429, text='private error details')])
        result = client.ask('Hello')
        self.assertEqual(result.errors, ['Cerebras HTTP 429'])
        self.assertEqual(len(self.requests), 1)

    def test_connect_retry_is_bounded_and_only_enabled_explicitly(self):
        for invalid in (True, False, -1, 2, '1', 1.0):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, 'connect_retries'):
                CerebrasClient(connect_retries=invalid)
        with patch('cerebras_client.api_key', return_value='offline-placeholder'), \
                patch('cerebras_client.httpx.HTTPTransport') as transport, \
                patch('cerebras_client.httpx.Client') as http:
            default = CerebrasClient()
            self.assertEqual(default.backend_info['connect_retries_configured'], 0)
            self.assertIsNone(http.call_args.kwargs['transport'])
            transport.assert_not_called()
            enabled = CerebrasClient(connect_retries=1)
            transport.assert_called_once_with(retries=1)
            self.assertIs(http.call_args.kwargs['transport'], transport.return_value)
            self.assertEqual(enabled.backend_info['connect_retries_configured'], 1)
            self.assertIn('ConnectError/ConnectTimeout only', enabled.backend_info['retry_policy'])

    def test_failed_followup_read_does_not_replay_completed_tool(self):
        call = {'index':0,'id':'face','function':{'name':'set_expression',
                'arguments':'{"expression":"curious"}'}}
        client = self.client([stream([{'tool_calls':[call]}], 'tool_calls'),
                              httpx.ReadError('offline injected read failure')], connect_retries=1)
        result = client.ask('Set a curious face')
        self.assertEqual(len(self.requests),2)
        self.assertEqual(len(client._bridge.calls),1)
        self.assertEqual(result.errors,['Cerebras timeout or transport failure: ReadError'])
        self.assertTrue(client.needs_reset)
        self.assertEqual(len([m for m in client.messages if m['role']=='tool']),1)
        self.assertTrue(client.ask('Continue').errors)
        self.assertEqual(len(self.requests),2)

    def test_opted_in_connection_retry_does_not_retry_http_status(self):
        client = self.client([httpx.Response(429, text='private error details')], connect_retries=1)
        result = client.ask('Hello')
        self.assertEqual(result.errors,['Cerebras HTTP 429'])
        self.assertEqual(len(self.requests),1)

    def test_duplicate_call_ids_do_not_execute(self):
        calls = [{'index':i,'id':'duplicate','function':{'name':'set_expression','arguments':'{"expression":"curious"}'}} for i in (0,1)]
        client = self.client([stream([{'tool_calls': calls}], 'tool_calls')])
        self.assertTrue(client.ask('Try').errors)
        self.assertEqual(client._bridge.calls, [])

    def test_stream_bounds_and_null_tool_delta(self):
        collect_delta({}, {'tool_calls':None})
        for part in [{'index':-1}, {'index':True}, {'index':0,'function':{'arguments':'a'*16385}}]:
            with self.assertRaises(ValueError):
                collect_delta({}, {'tool_calls':[part]})

    def test_new_session_clears_history(self):
        client = self.client([stream([{'content':'Hello.'}])])
        client.ask('Hi')
        old = client.thread_id
        client.new_session()
        self.assertNotEqual(old, client.thread_id)
        self.assertEqual(client.messages, [{'role':'system','content':'MIST'}])

    def test_partial_visible_output_is_retained_and_followup_requires_reset(self):
        client = self.client([stream([{'content':'Four.'}], done=False)])
        events = []
        result = client.ask('Two plus two?', on_event=events.append)
        self.assertTrue(result.errors)
        self.assertEqual(result.text, '')
        self.assertEqual(result.timings['streamed_visible_text'], 'Four.')
        self.assertTrue(events)
        self.assertTrue(client.ask('Continue').errors)
        self.assertEqual(len(self.requests),1)
        self.assertEqual([m['role'] for m in client.messages], ['system','user'])
        client.new_session()
        self.assertFalse(client.needs_reset)

    def test_partial_tool_round_cannot_be_replayed_by_followup(self):
        calls=[{'index':i,'id':'c'+str(i),'function':{'name':'set_expression','arguments':'{"expression":"curious"}'}} for i in (0,1)]
        client=self.client([stream([{'tool_calls':calls}], 'tool_calls')])
        bridge=client._bridge
        original=bridge.request
        def partial(operation,args):
            if bridge.calls:
                raise TimeoutError('Injected second-call failure')
            return original(operation,args)
        bridge.request=partial
        result=client.ask('Try both')
        self.assertTrue(result.errors)
        self.assertEqual(len(bridge.calls),1)
        self.assertEqual(len(result.tool_calls),1)
        self.assertEqual(len([m for m in client.messages if m['role']=='tool']),1)
        self.assertTrue(client.ask('Continue').errors)
        self.assertEqual(len(self.requests),1)

    def test_model_specific_controls_reject_unsupported_efforts(self):
        for effort in ('low','medium','high'):
            self.assertEqual(request_controls('gpt-oss-120b',effort),
                             {'reasoning_effort':effort,'reasoning_format':'hidden'})
        for effort in ('none','low','medium','high'):
            self.assertEqual(request_controls('qwen-3.8-27b',effort),
                             {'reasoning_effort':effort,'reasoning_format':'parsed','clear_thinking':True})
        for model, effort in [('gpt-oss-120b','none'),('qwen-3.8-27b','xhigh'),
                              ('qwen-3.8-27b','off'),('qwen-3.5-27b','low')]:
            with self.assertRaises(ValueError):request_controls(model,effort)

    def test_qwen_discards_private_reasoning_across_tool_rounds_and_history(self):
        client=self.client([
            stream([{'reasoning':'PRIVATE_QWEN_THOUGHT'}, {'reasoning_content':'PRIVATE_OTHER_FIELD'},
                    {'tool_calls':[{'index':0,'id':'q1','function':{'name':'set_expression','arguments':'{"expression":"curious"}'}}]}], 'tool_calls'),
            stream([{'reasoning':'PRIVATE_SECOND_ROUND'},{'content':'Applied.'}]),
            stream([{'content':'Curious.'}])], model='qwen-3.8-27b',thinking='high')
        events=[]
        result=client.ask('Use curious.',on_event=events.append)
        self.assertFalse(result.errors)
        self.assertEqual(result.text,'Applied.')
        self.assertEqual(len(client._bridge.calls),1)
        client.ask('Which face?',on_event=events.append)
        captured=json.dumps(events)+json.dumps(client.messages)+json.dumps(asdict(result))+json.dumps(self.requests)
        self.assertNotIn('PRIVATE_',captured)
        for request in self.requests:
            self.assertEqual(request['reasoning_format'],'parsed')
            self.assertEqual(request['reasoning_effort'],'high')
            self.assertTrue(request['clear_thinking'])
            self.assertFalse(request['parallel_tool_calls'])

    def test_qwen_none_and_invalid_tools_keep_the_application_boundary(self):
        client=self.client([
            stream([{'tool_calls':[{'index':0,'id':'q1','function':{'name':'shell','arguments':'{}'}}]}], 'tool_calls'),
            stream([{'content':'Unavailable.'}])],model='qwen-3.8-27b',thinking='none')
        result=client.ask('Try.')
        self.assertEqual(self.requests[0]['reasoning_effort'],'none')
        self.assertEqual(client._bridge.calls,[])
        self.assertTrue(result.tool_calls[0].is_error)
        self.assertEqual(result.text,'Unavailable.')

    def test_batch_selection_opt_in_executes_sequentially_with_receipt_history(self):
        # Interleaved provider deltas still execute in declared index order.
        calls=self.batch()
        client=self.client([stream([{'tool_calls':[calls[1]]},{'tool_calls':[calls[0]]}], 'tool_calls'),
                            stream([{'content':'Applied; no hardware connected.'}])],
                           model='qwen-3.8-27b',thinking='low',parallel_tool_calls=True)
        self.add_sensor(client)
        events=[]
        result=client.ask('Look curious and read sensors.',on_event=events.append)
        self.assertEqual(result.errors,[])
        self.assertTrue(all(r['parallel_tool_calls'] for r in self.requests))
        self.assertTrue(client.backend_info['parallel_tool_calls'])
        self.assertEqual([c[1]['name'] for c in client._bridge.calls],['set_expression','get_sensor_snapshot'])
        tool_events=[(e['type'],e['toolName']) for e in events if e['type'].startswith('tool_execution_')]
        self.assertEqual(tool_events,[('tool_execution_start','set_expression'),('tool_execution_end','set_expression'),
                                      ('tool_execution_start','get_sensor_snapshot'),('tool_execution_end','get_sensor_snapshot')])
        history=self.requests[1]['messages']
        self.assertEqual([m['role'] for m in history],['system','user','assistant','tool','tool'])
        self.assertEqual([m['tool_call_id'] for m in history if m['role']=='tool'],['face','sensor'])
        self.assertEqual(len(result.timings['http_requests']),2)
        self.assertEqual([r['selected_tool_calls'] for r in result.timings['http_requests']],[2,0])

    def test_batch_each_call_keeps_allowlist_and_schema_checks(self):
        for invalid in ('bad_schema','unlisted'):
            with self.subTest(invalid=invalid):
                calls=self.batch()
                if invalid=='bad_schema':calls[0]['function']['arguments']='{"expression":"evil"}'
                else:calls[0]['function']['name']='shell'
                client=self.client([stream([{'tool_calls':calls}],'tool_calls'),stream([{'content':'The sensor read completed; the first action failed.'}])],
                                   parallel_tool_calls=True)
                self.add_sensor(client)
                result=client.ask('Try both.')
                self.assertEqual(result.errors,[])
                self.assertEqual([c[1]['name'] for c in client._bridge.calls],['get_sensor_snapshot'])
                self.assertEqual([c.is_error for c in result.tool_calls],[True,False])
                self.assertIn('Tool rejected',self.requests[1]['messages'][3]['content'])

    def test_stale_batch_stops_before_later_actions(self):
        class StaleTurn(RuntimeError):
            pass
        calls=self.batch()+[{'index':2,'id':'later','function':{'name':'set_expression','arguments':'{"expression":"curious"}'}}]
        client=self.client([stream([{'tool_calls':calls}],'tool_calls')],parallel_tool_calls=True)
        self.add_sensor(client)
        bridge=client._bridge
        request=bridge.request
        attempts=[]
        def stale_after_first(operation,args):
            attempts.append(args['name'])
            if bridge.calls:raise StaleTurn('Superseded user turn; no tool executed')
            return request(operation,args)
        bridge.request=stale_after_first
        result=client.ask('Try the actions.')
        self.assertTrue(result.errors)
        self.assertEqual(attempts,['set_expression','get_sensor_snapshot'])
        self.assertEqual([c[1]['name'] for c in bridge.calls],['set_expression'])
        self.assertEqual(len(result.tool_calls),1)
        self.assertEqual(len([m for m in client.messages if m['role']=='tool']),1)
        self.assertTrue(client.ask('Continue').errors)
        self.assertEqual(len(self.requests),1)

    def test_batch_selection_requires_boolean_before_http_creation(self):
        for value in (None,0,1,'true',[],{}):
            with self.subTest(value=value), patch('cerebras_client.httpx.Client') as http, patch('cerebras_client.api_key') as key:
                with self.assertRaisesRegex(ValueError,'parallel_tool_calls must be a boolean'):
                    CerebrasClient(parallel_tool_calls=value)
                http.assert_not_called()
                key.assert_not_called()

    def test_opt_in_does_not_relax_stream_call_bound(self):
        calls=[{'index':i,'id':str(i),'function':{'name':'set_expression','arguments':'{"expression":"curious"}'}} for i in range(17)]
        client=self.client([stream([{'tool_calls':calls}],'tool_calls')],parallel_tool_calls=True)
        result=client.ask('Try too many.')
        self.assertTrue(result.errors)
        self.assertEqual(client._bridge.calls,[])


if __name__ == '__main__':
    unittest.main()
