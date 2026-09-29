import asyncio,json,sys,tempfile,threading,time,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from duplex.background import BackgroundBrain,ReadBridge,CONTEXT_LIMIT,ANALYST_PROMPT_VERSION,result_diagnostic
from duplex.runtime import RobotRuntime
from duplex.server import Conversation
from codex_client import restricted_config

class Tests(unittest.IsolatedAsyncioTestCase):
    def test_web_search_opt_in_keeps_host_tools_disabled(self):
        self.assertEqual(restricted_config()['web_search'],'disabled')
        config=restricted_config(web_search=True)
        self.assertEqual(config['web_search'],'live')
        for key in ('features.shell_tool','features.unified_exec','features.apps',
                    'features.plugins','features.multi_agent','features.browser_use'):
            self.assertFalse(config[key])

    async def test_two_independent_jobs_run_without_blocking_and_third_is_bounded(self):
        gate=asyncio.Event();entered=[]
        async def runner(job):
            entered.append(job['job_id'])
            await gate.wait()
            return job['question'],0
        async def emit(event):pass
        with tempfile.TemporaryDirectory() as d:
            brain=BackgroundBrain(RobotRuntime(d),d,emit,lambda:1,runner)
            first=await asyncio.wait_for(brain.handle('start_background_task',{'question':'First'}),.1)
            second=await asyncio.wait_for(brain.handle('start_background_task',{'question':'Second'}),.1)
            await asyncio.sleep(0)
            self.assertNotEqual(first['job_id'],second['job_id'])
            self.assertEqual(len(entered),2)
            third=await brain.handle('start_background_task',{'question':'Third'})
            self.assertEqual(third['status'],'busy')
            self.assertEqual(len(brain.tasks),2)
            gate.set();await asyncio.gather(*list(brain.tasks.values()))
            await brain.close()

    async def test_web_research_requires_native_search_receipt(self):
        async def emit(event):pass
        with tempfile.TemporaryDirectory() as d:
            brain=BackgroundBrain(RobotRuntime(d),d,emit,lambda:1)
            calls=[]
            def ask(request,**kwargs):
                return SimpleNamespace(text='Claim with https://example.com',errors=[],tool_calls=[],
                    timings={'native_web_search_calls':calls[-1]})
            client=SimpleNamespace(new_session=Mock(),ask=ask,close=Mock())
            with patch.object(brain,'create_client',return_value=client):
                calls.append(0)
                missing=await brain.handle('start_background_task',{'question':'Current claim','task_type':'web_research'})
                await asyncio.gather(*list(brain.tasks.values()))
                self.assertEqual(brain.jobs[missing['job_id']]['status'],'failed')
                calls.append(1)
                verified=await brain.handle('start_background_task',{'question':'Another claim','task_type':'web_research'})
                await asyncio.gather(*list(brain.tasks.values()))
                self.assertEqual(brain.jobs[verified['job_id']]['status'],'complete')
                self.assertEqual(brain.jobs[verified['job_id']]['tool_calls'],1)
            await brain.close()

    async def test_cerebras_analysis_preset_routes_web_research_to_luna(self):
        events=[]
        async def emit(event):events.append(event)
        answer=SimpleNamespace(text='Verified at https://example.org/',errors=[],tool_calls=[],
                               timings={'native_web_search_calls':1})
        client=SimpleNamespace(new_session=Mock(),ask=Mock(return_value=answer),close=Mock())
        with tempfile.TemporaryDirectory() as d:
            brain=BackgroundBrain(RobotRuntime(d),d,emit,lambda:1,
                                  provider='cerebras',model='gpt-oss-120b',reasoning_effort='high')
            with patch('codex_client.CodexClient',return_value=client) as codex, \
                 patch('benchmarks.naturalness.cerebras_client.CerebrasClient') as cerebras, \
                 patch('duplex.native.choose_binary'):
                receipt=await brain.handle('start_background_task',
                    {'question':'Find an official source','task_type':'web_research'})
                await asyncio.gather(*list(brain.tasks.values()))
            self.assertEqual(brain.jobs[receipt['job_id']]['status'],'complete')
            self.assertEqual(receipt['provider'],'codex')
            self.assertEqual(receipt['model'],'gpt-6-luna')
            self.assertEqual(codex.call_args.kwargs['model'],'gpt-6-luna')
            self.assertEqual(codex.call_args.kwargs['thinking'],'low')
            self.assertEqual(codex.call_args.kwargs['max_output_tokens'],360)
            self.assertTrue(codex.call_args.kwargs['web_search'])
            cerebras.assert_not_called()
            started=next(event for event in events if event.get('phase')=='started')
            self.assertEqual((started['provider'],started['model'],started['reasoning_effort']),
                             ('codex','gpt-6-luna','low'))
            await brain.close()

    async def test_deadline_fails_public_job_without_waiting_for_runner(self):
        gate=asyncio.Event();events=[]
        async def runner(job):await gate.wait();return 'Late conclusion',0
        async def emit(event):events.append(event)
        with tempfile.TemporaryDirectory() as d,patch('duplex.background.JOB_DEADLINE_S',.02):
            brain=BackgroundBrain(RobotRuntime(d),d,emit,lambda:1,runner)
            receipt=await brain.handle('start_background_task',{'question':'Slow'})
            await asyncio.sleep(.05)
            status=await brain.handle('background_task_status',{'job_id':receipt['job_id']})
            self.assertEqual(status['status'],'failed')
            self.assertIn('timed out',status['summary'])
            gate.set();await asyncio.gather(*list(brain.tasks.values()))
            self.assertFalse(any(event.get('generated_text')=='Late conclusion' for event in events))
            await brain.close()

    def test_outcome_diagnostics_allow_only_safe_numeric_metadata_and_known_errors(self):
        result=SimpleNamespace(errors=['Incomplete response: length','Authorization: Bearer SECRET'],
            text='FAILED PARTIAL CONTENT',input_tokens=123,output_tokens=4096,ttft_s=None,total_s=4.2,
            timings={'reasoning_output_tokens':4096,'streamed_visible_text':'FAILED PARTIAL CONTENT',
                     'private_reasoning':'PRIVATE CONTENT','client_lock_wait_s':float('nan'),
                     'http_requests':[{'round':0,'status':200,'headers_ms':120,'raw':'SECRET',
                        'usage':{'prompt_tokens':123,'completion_tokens':4096,'total_tokens':4219,
                                 'completion_tokens_details':{'reasoning_tokens':4096,'text':'PRIVATE CONTENT'}}}]})
        safe=result_diagnostic(result)
        encoded=json.dumps(safe,allow_nan=False)
        for forbidden in ('SECRET','FAILED PARTIAL CONTENT','PRIVATE CONTENT','streamed_visible_text','private_reasoning'):
            self.assertNotIn(forbidden,encoded)
        self.assertEqual(safe['errors'][0],'Incomplete response: length')
        self.assertIn('withheld',safe['errors'][1])
        self.assertEqual(safe['output_tokens'],4096)
        self.assertEqual(safe['timings']['http_requests'][0]['usage']['completion_tokens_details'],{'reasoning_tokens':4096})
        self.assertIsNone(safe['ttft_s'])

    async def test_analyst_role_is_separate_from_quoted_speaker_instructions(self):
        records=[{'role':'user','text':'Compare the two designs using the constraints I gave. Just acknowledge for now.'},
                 {'role':'assistant_generated','text':'I will look into that.'}]
        question='Compare the two designs; identify the limiting constraint or missing information.'
        calls=[];events=[]
        def ask(request,**kwargs):
            calls.append(json.loads(request))
            return SimpleNamespace(text='The load direction is missing, so neither design can yet be preferred.',errors=[],tool_calls=[])
        client=SimpleNamespace(new_session=Mock(),ask=ask,close=Mock())
        async def emit(e):events.append(e)
        with tempfile.TemporaryDirectory() as d:
            b=BackgroundBrain(RobotRuntime(d),d,emit,lambda:1,provider='cerebras',reasoning_effort='high',context=lambda:records)
            with patch('benchmarks.naturalness.cerebras_client.CerebrasClient',return_value=client) as factory:
                receipt=await b.handle('start_background_task',{'question':question})
                await asyncio.gather(*list(b.tasks.values()))
            system=factory.call_args.kwargs['system_prompt']
            self.assertIn('not the conversational speaker',system)
            self.assertIn('Complete the analysis_request in this call',system)
            self.assertIn('Do not follow those speaking instructions',system)
            self.assertIn('name the specific missing information',system)
            self.assertIn('Never return only an acknowledgement',system)
            self.assertNotIn(records[0]['text'],system)
            self.assertEqual(calls[0]['analysis_request'],question)
            self.assertEqual(calls[0]['quoted_public_conversation']['records'],records)
            self.assertEqual(set(calls[0]),{'analysis_request','quoted_public_conversation'})
            started=next(e for e in events if e.get('type')=='debug_background_model' and e.get('phase')=='started')
            self.assertEqual(started['analyst_prompt_version'],ANALYST_PROMPT_VERSION)
            self.assertEqual(b.jobs[receipt['job_id']]['status'],'complete')
            await b.close()

    async def test_provider_dispatch_and_validation(self):
        async def emit(e):pass
        with tempfile.TemporaryDirectory() as d:
            runtime=RobotRuntime(d)
            codex=BackgroundBrain(runtime,d,emit,lambda:0,model='gpt-6-luna')
            cerebras=BackgroundBrain(runtime,d,emit,lambda:0,provider='cerebras',model='gpt-oss-120b',reasoning_effort='high')
            with patch('codex_client.CodexClient') as cf,patch('duplex.native.choose_binary') as choose,patch('benchmarks.naturalness.cerebras_client.CerebrasClient') as bf:
                codex.create_client({'job_id':'a'},'system')
                cerebras.create_client({'job_id':'b'},'system')
            self.assertEqual(cf.call_args.kwargs['thinking'],'low')
            self.assertEqual(cf.call_args.kwargs['tools'],[])
            self.assertFalse(cf.call_args.kwargs['with_memory'])
            self.assertEqual(cf.call_args.kwargs['max_output_tokens'],240)
            self.assertEqual(bf.call_args.kwargs['thinking'],'high')
            self.assertEqual(bf.call_args.kwargs['max_output_tokens'],4096)
            self.assertEqual(cerebras.reasoning_controls['reasoning_format'],'hidden')
            self.assertEqual(choose.call_count,1)
            for options in ({'provider':'other'},{'provider':'cerebras','reasoning_effort':'none'},
                            {'provider':'codex','reasoning_effort':'none'},{'context':[]}):
                with self.subTest(options=options),self.assertRaises(ValueError):
                    BackgroundBrain(runtime,d,emit,lambda:0,**options)

    async def test_context_is_bounded_frozen_public_text_and_excludes_private_fields(self):
        records=[{'role':'user','text':'old '+str(i)+'x'*350,'private_reasoning':'NEVER SEND'} for i in range(30)]
        records+=[{'role':'user','text':'No, by cloud I meant the vendor Claude.'},
                  {'role':'assistant_generated','text':'Generated but unplayed.','playback_verified':True}]
        gate=asyncio.Event()
        async def runner(job):await gate.wait();return 'Result',0
        async def emit(e):pass
        with tempfile.TemporaryDirectory() as d:
            b=BackgroundBrain(RobotRuntime(d),d,emit,lambda:5,runner,context=lambda:records)
            receipt=await b.handle('start_background_task',{'question':'Use my correction'})
            snapshot=b.jobs[receipt['job_id']]['context']
            original=json.dumps(snapshot,ensure_ascii=False)
            self.assertLessEqual(len(original),CONTEXT_LIMIT)
            self.assertGreater(snapshot['omitted_records'],0)
            self.assertEqual(snapshot['records'][-2]['text'],records[-2]['text'])
            self.assertNotIn('NEVER SEND',original)
            self.assertNotIn('playback_verified',original)
            self.assertIn('playback is unverified',original)
            records[-2]['text']='Later mutation'
            self.assertEqual(json.dumps(snapshot,ensure_ascii=False),original)
            self.assertEqual(receipt['revision'],5)
            gate.set();await asyncio.gather(*list(b.tasks.values()));await b.close()
            b=BackgroundBrain(RobotRuntime(d),d,emit,lambda:5,runner,context=lambda:{'bad':'type'})
            with self.assertRaises(ValueError):await b.handle('start_background_task',{'question':'Question'})
            self.assertFalse(b.jobs);self.assertFalse(b.tasks)

    async def test_changed_revision_retains_result_for_explicit_read_without_public_output(self):
        gate=asyncio.Event();events=[];revision=[1]
        async def runner(job):await gate.wait();return 'Obsolete conclusion',0
        async def emit(e):events.append(e)
        with tempfile.TemporaryDirectory() as d:
            b=BackgroundBrain(RobotRuntime(d),d,emit,lambda:revision[0],runner)
            receipt=await b.handle('start_background_task',{'question':'Original question'})
            await asyncio.sleep(0);revision[0]=2;gate.set()
            await asyncio.gather(*list(b.tasks.values()))
            self.assertEqual(b.jobs[receipt['job_id']]['status'],'stale')
            status=await b.handle('background_task_status',{'job_id':receipt['job_id']})
            self.assertEqual(status['summary'],'Obsolete conclusion')
            self.assertTrue(status['context_changed'])
            self.assertEqual(status['question'],'Original question')
            self.assertFalse(any(e.get('generated_text') for e in events))
            await b.close()

    async def test_completed_old_result_remains_readable_as_stale_history(self):
        revision=[1]
        async def runner(job):return 'Completed under the earlier question.',0
        async def emit(e):pass
        with tempfile.TemporaryDirectory() as d:
            b=BackgroundBrain(RobotRuntime(d),d,emit,lambda:revision[0],runner)
            receipt=await b.handle('start_background_task',{'question':'Earlier question'})
            await asyncio.gather(*list(b.tasks.values()))
            revision[0]=2
            historical=await b.handle('background_task_status',{'job_id':receipt['job_id']})
            self.assertEqual(historical['status'],'stale')
            self.assertEqual(historical['summary'],'Completed under the earlier question.')
            self.assertIn('historical',historical['note'])
            self.assertEqual(historical['revision'],1)
            self.assertFalse(b.jobs[receipt['job_id']]['announced'])
            await b.close()

    async def test_read_bridge_rejects_actions_and_obsolete_reads(self):
        calls=[];valid=[True]
        runtime=SimpleNamespace(call=lambda name,args:calls.append(name) or {'ok':True})
        bridge=ReadBridge(runtime,asyncio.get_running_loop(),lambda:valid[0])
        with self.assertRaises(ValueError):
            await asyncio.to_thread(bridge.request,'call',{'name':'walk','arguments':{}})
        valid[0]=False
        with self.assertRaises(RuntimeError):
            await asyncio.to_thread(bridge.request,'call',{'name':'get_sensor_snapshot','arguments':{}})
        self.assertEqual(calls,[])
        valid[0]=True
        receipt=await asyncio.to_thread(bridge.request,'call',{'name':'recall','arguments':{}})
        self.assertFalse(receipt['isError']);self.assertEqual(calls,['recall'])

    async def test_cancel_during_client_creation_closes_without_asking(self):
        entered=threading.Event();release=threading.Event();events=[]
        client=SimpleNamespace(new_session=Mock(),ask=Mock(),close=Mock())
        def factory(job,prompt):entered.set();release.wait(3);return client
        async def emit(e):events.append(e)
        with tempfile.TemporaryDirectory() as d:
            b=BackgroundBrain(RobotRuntime(d),d,emit,lambda:1,provider='cerebras')
            with patch.object(b,'create_client',side_effect=factory):
                receipt=await b.handle('start_background_task',{'question':'Reason about this'})
                self.assertTrue(await asyncio.to_thread(entered.wait,1))
                cancelled=await asyncio.wait_for(b.handle('cancel_background_task',{}),.2)
                self.assertEqual(cancelled['status'],'cancelled')
                self.assertFalse(cancelled['cancellation']['remote_stop_verified'])
                release.set();await asyncio.gather(*list(b.tasks.values()))
            client.ask.assert_not_called();client.close.assert_called_once()
            self.assertFalse(b.clients);self.assertFalse(any(e.get('generated_text') for e in events))
            await b.close()

    async def test_close_joins_client_creation_race(self):
        entered=threading.Event();release=threading.Event()
        client=SimpleNamespace(new_session=Mock(),ask=Mock(),close=Mock())
        def factory(job,prompt):entered.set();release.wait(3);return client
        async def emit(e):pass
        with tempfile.TemporaryDirectory() as d:
            b=BackgroundBrain(RobotRuntime(d),d,emit,lambda:1)
            with patch.object(b,'create_client',side_effect=factory):
                await b.handle('start_background_task',{'question':'Reason about this'})
                self.assertTrue(await asyncio.to_thread(entered.wait,1))
                closing=asyncio.create_task(b.close());await asyncio.sleep(.02)
                self.assertFalse(closing.done())
                release.set();await asyncio.wait_for(closing,1)
            client.ask.assert_not_called();client.close.assert_called_once()
            self.assertFalse(b.clients);self.assertFalse(b.tasks)

    async def test_cancel_without_remote_api_suppresses_output_and_bounds_concurrency(self):
        entered=threading.Event();release=threading.Event();events=[];prompts=[]
        def ask(prompt,**kwargs):
            prompts.append(prompt);entered.set();release.wait(3)
            kwargs['on_event']({'type':'message_update','assistantMessageEvent':{'type':'text_delta','delta':'Discard this'}})
            return SimpleNamespace(text='Discard this',errors=[],tool_calls=[])
        client=SimpleNamespace(new_session=Mock(),ask=ask,close=Mock())
        async def emit(e):events.append(e)
        with tempfile.TemporaryDirectory() as d:
            b=BackgroundBrain(RobotRuntime(d),d,emit,lambda:1,provider='cerebras',context=lambda:[{'role':'user','text':'Earlier correction'}])
            with patch.object(b,'create_client',return_value=client):
                started=await b.handle('start_background_task',{'question':'Original'})
                self.assertTrue(await asyncio.to_thread(entered.wait,1))
                cancelled=await asyncio.wait_for(b.handle('cancel_background_task',{}),.2)
                self.assertFalse(cancelled['cancellation']['remote_stop_requested'])
                another=await b.handle('start_background_task',{'question':'Replacement'})
                self.assertNotEqual(another['job_id'],started['job_id'])
                release.set();await asyncio.gather(*list(b.tasks.values()))
            self.assertEqual(client._selected,{'get_sensor_snapshot','recall'})
            self.assertEqual({x['name'] for x in client._specs},client._selected)
            self.assertIn('Earlier correction',prompts[0])
            self.assertFalse(any(e.get('job_id')==started['job_id'] and (e.get('generated_text') or e.get('delta')) for e in events))
            self.assertEqual(client.close.call_count,2);await b.close()

    async def test_failed_job_never_emits_provisional_generated_text(self):
        events=[]
        def ask(prompt,**kwargs):
            kwargs['on_event']({'type':'message_update','assistantMessageEvent':{'type':'text_delta','delta':'Partial failed output'}})
            return SimpleNamespace(text='Partial failed output',errors=['Incomplete response: length'],tool_calls=[],
                output_tokens=4096,input_tokens=700,total_s=4.2,timings={'reasoning_output_tokens':4096,'streamed_visible_text':'Partial failed output'})
        client=SimpleNamespace(new_session=Mock(),ask=ask,close=Mock())
        async def emit(e):events.append(e)
        with tempfile.TemporaryDirectory() as d:
            b=BackgroundBrain(RobotRuntime(d),d,emit,lambda:1)
            with patch.object(b,'create_client',return_value=client):
                receipt=await b.handle('start_background_task',{'question':'Original'})
                await asyncio.gather(*list(b.tasks.values()))
            self.assertEqual(b.jobs[receipt['job_id']]['status'],'failed')
            self.assertFalse(any(e.get('generated_text') or e.get('delta') for e in events))
            outcome=next(e for e in events if e.get('phase')=='outcome')
            self.assertEqual(outcome['errors'],['Incomplete response: length'])
            self.assertEqual(outcome['output_tokens'],4096)
            self.assertEqual(outcome['status'],'failed')
            self.assertNotIn('streamed_visible_text',outcome['timings'])
            cancelled=await b.handle('cancel_background_task',{'job_id':receipt['job_id']})
            self.assertEqual(cancelled['status'],'cancelled')
            self.assertNotIn('summary',cancelled)
            await b.close()

    async def test_completed_announced_job_can_still_be_cancelled(self):
        async def runner(job):return 'Result',0
        async def emit(e):pass
        with tempfile.TemporaryDirectory() as d:
            b=BackgroundBrain(RobotRuntime(d),d,emit,lambda:4,runner)
            receipt=await b.handle('start_background_task',{'question':'Question'})
            await asyncio.gather(*list(b.tasks.values()))
            b.jobs[receipt['job_id']]['announced']=True
            cancelled=await b.handle('cancel_background_task',{'job_id':receipt['job_id']})
            self.assertEqual(cancelled['status'],'cancelled')
            self.assertNotIn('summary',cancelled)
            await b.close()

    async def test_close_waits_for_inflight_request_before_client_cleanup(self):
        entered=threading.Event();release=threading.Event();finished=threading.Event()
        def ask(prompt,**kwargs):
            entered.set();release.wait(3);finished.set()
            return SimpleNamespace(text='Finished after close',errors=[],tool_calls=[])
        def cleanup():self.assertTrue(finished.is_set())
        client=SimpleNamespace(new_session=Mock(),ask=ask,close=Mock(side_effect=cleanup))
        events=[]
        async def emit(e):events.append(e)
        with tempfile.TemporaryDirectory() as d:
            b=BackgroundBrain(RobotRuntime(d),d,emit,lambda:1,provider='cerebras')
            with patch.object(b,'create_client',return_value=client):
                await b.handle('start_background_task',{'question':'Question'})
                self.assertTrue(await asyncio.to_thread(entered.wait,1))
                closing=asyncio.create_task(b.close());await asyncio.sleep(.02)
                self.assertFalse(closing.done());client.close.assert_not_called()
                release.set();await asyncio.wait_for(closing,1)
            client.close.assert_called_once()
            self.assertFalse(b.tasks);self.assertFalse(b.clients)
            self.assertFalse(any(e.get('generated_text') for e in events))

    async def test_failed_delivery_remains_retryable_and_is_not_marked_announced(self):
        attempts=[];events=[]
        class Socket:
            closed=False
            async def send_json(self,e):events.append(e)
        class Voice:
            async def context(self,text):
                attempts.append(text)
                if len(attempts)==1:raise RuntimeError('Temporary delivery failure')
        with tempfile.TemporaryDirectory() as d:
            c=Conversation({'runtime':RobotRuntime(d)},Socket());c.voice=Voice()
            c.mask=SimpleNamespace(pending={},diagnostic=lambda *a,**kw:None)
            job={'job_id':'job','status':'complete','revision':0,'announced':False,'summary':'Ready'}
            c.brain.jobs['job']=job
            task=asyncio.create_task(c.deliver_results())
            try:
                await asyncio.sleep(.25)
                self.assertFalse(job['announced']);self.assertEqual(len(attempts),1)
                job['retry_delivery_at']=0
                await asyncio.sleep(.25)
                self.assertTrue(job['announced']);self.assertEqual(len(attempts),2)
                queued=next(e for e in events if e['type']=='brain_delivery')
                self.assertFalse(queued['playback_verified'])
            finally:
                task.cancel();await asyncio.gather(task,return_exceptions=True);await c.brain.close()
    async def test_accepts_immediately_and_deduplicates(self):
        gate=asyncio.Event();events=[]
        async def runner(job):await gate.wait();return 'Result',0
        async def emit(e):events.append(e)
        with tempfile.TemporaryDirectory() as d:
            b=BackgroundBrain(RobotRuntime(d),d,emit,lambda:1,runner)
            r=await asyncio.wait_for(b.handle('start_background_task',{'question':'Compare options'}),.1)
            self.assertEqual(r['status'],'running')
            self.assertEqual((await b.handle('start_background_task',{'question':'Compare options'}))['job_id'],r['job_id'])
            gate.set();await asyncio.gather(*list(b.tasks.values()))
            self.assertEqual((await b.handle('background_task_status',{}))['summary'],'Result')
            self.assertNotEqual((await b.handle('start_background_task',{'question':'Compare the same options again'}))['job_id'],r['job_id'])
            await b.close()
    async def test_cancelled_result_never_publishes(self):
        gate=asyncio.Event();events=[]
        async def runner(job):await gate.wait();return 'Old result',0
        async def emit(e):events.append(e)
        with tempfile.TemporaryDirectory() as d:
            b=BackgroundBrain(RobotRuntime(d),d,emit,lambda:1,runner)
            await b.handle('start_background_task',{'question':'Old question'})
            await b.handle('cancel_background_task',{})
            gate.set();await asyncio.gather(*list(b.tasks.values()))
            self.assertFalse(any(e.get('status')=='complete' for e in events));await b.close()
    async def test_mic_and_result_delivery_are_independent(self):
        packets=[];delivered=[]
        class Socket:
            closed=False
            async def send_json(self,e):pass
        class Voice:
            track=SimpleNamespace(append=packets.append)
            async def context(self,text):delivered.append(text)
        with tempfile.TemporaryDirectory() as d:
            c=Conversation({'runtime':RobotRuntime(d)},Socket());c.voice=Voice();c.mask=SimpleNamespace(pending={})
            c.brain.jobs['job']={'job_id':'job','status':'complete','revision':0,'announced':False,'summary':'Ready','elapsed_s':1,'tool_calls':0}
            c.playback_busy=True;task=asyncio.create_task(c.deliver_results())
            await c.handle({'type':'mic','pcm':'AQA='});await asyncio.sleep(.22)
            self.assertEqual(packets,[b'\1\0']);self.assertEqual(delivered,[])
            c.playback_busy=False;c.user_revision=1;await asyncio.sleep(.22)
            self.assertEqual(delivered,[],'a changed topic must not get an unsolicited old result')
            c.user_revision=0;c.user_speaking=True;await asyncio.sleep(.22);self.assertEqual(delivered,[])
            c.user_speaking=False;await asyncio.sleep(.22);self.assertEqual(len(delivered),1)
            task.cancel();await asyncio.gather(task,return_exceptions=True);await c.brain.close()

if __name__=='__main__':unittest.main(verbosity=2)
