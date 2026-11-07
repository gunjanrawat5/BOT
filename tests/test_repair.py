import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
from agent.repair import RepairDecision, FileEdit, repair, apply_repair, safe_path
from agent.brain import response_schema, DeadlineExceeded


class RepairTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.repo=self.root/'game';self.repo.mkdir()
        (self.repo/'src').mkdir()
        (self.repo/'src'/'math.js').write_text('export function add(a,b){return a-b;}\n')
        (self.repo/'package.json').write_text(json.dumps({'type':'module','scripts':{'test':'node --test','build':'vite build'}}))
        (self.repo/'.env').write_text('GEMINI_API_KEY=do-not-copy')
        vite=self.repo/'node_modules'/'vite'/'bin';vite.mkdir(parents=True)
        (vite/'vite.js').write_text("console.log('fixture build passed')")
        folder=self.root/'reports'/'bug';folder.mkdir(parents=True)
        self.report=folder/'bug.json';self.report.write_text('{"title":"Addition subtracts"}')
        self.output=self.root/'repairs'/'one'
        self.brain=Mock();self.brain.model='test-model'

    def tearDown(self): self.temp.cleanup()

    def proposal(self):
        return RepairDecision(decision='propose_fix',reasoning='src/math.js subtracts instead of adding; regression asserts a known sum.',paths=[],edits=[
            FileEdit(path='src/math.js',old_text='return a-b;',new_text='return a+b;'),
            FileEdit(path='tests/add.test.js',old_text='',new_text="import test from 'node:test';import assert from 'node:assert/strict';import {add} from '../src/math.js';test('addition',()=>assert.equal(add(2,3),5));")])

    def test_snapshot_fix_red_green_patch_and_explicit_apply(self):
        self.brain._request.side_effect=[RepairDecision(decision='read_files',reasoning='Inspect math',paths=['src/math.js'],edits=[]),self.proposal()]
        result=repair(self.repo,self.report,self.output,self.brain)
        self.assertEqual(result['status'],'checks_passed')
        self.assertIn('a-b',(self.repo/'src'/'math.js').read_text())
        self.assertIn('a+b',(self.output/'workspace'/'src'/'math.js').read_text())
        self.assertFalse((self.output/'workspace'/'.env').exists())
        self.assertFalse((self.output/'workspace'/'node_modules').exists())
        self.assertIn('tests/add.test.js',(self.output/'fix.patch').read_text())
        stages={r['stage']:r['exit_code'] for r in result['checks']}
        self.assertNotEqual(stages['regression_before_fix'],0)
        self.assertEqual(stages['tests_after_fix'],0)
        self.assertEqual(apply_repair(self.output)['status'],'applied')
        self.assertIn('a+b',(self.repo/'src'/'math.js').read_text())

    def test_stale_source_blocks_apply(self):
        self.brain._request.return_value=self.proposal()
        repair(self.repo,self.report,self.output,self.brain)
        (self.repo/'src'/'math.js').write_text('new user work')
        with self.assertRaisesRegex(ValueError,'Original file changed'):
            apply_repair(self.output)
        self.assertEqual((self.repo/'src'/'math.js').read_text(),'new user work')

    def test_false_positive_does_not_edit_source(self):
        self.brain._request.return_value=RepairDecision(decision='not_a_bug',reasoning='Game design accounts for this behavior.',paths=[],edits=[])
        result=repair(self.repo,self.report,self.output,self.brain)
        self.assertEqual(result['status'],'not_a_bug')
        self.assertEqual(result['changes'],[])
        self.assertFalse((self.output/'fix.patch').exists())

    def test_non_reproducing_test_is_not_a_validated_fix(self):
        decision=self.proposal()
        decision.edits[1].new_text="import test from 'node:test';test('no assertion',()=>{});"
        self.brain._request.side_effect=[decision, DeadlineExceeded()]
        result=repair(self.repo,self.report,self.output,self.brain)
        self.assertEqual(result['status'],'inconclusive')
        self.assertFalse((self.output/'workspace'/'tests'/'add.test.js').exists())

    def test_paths_and_nested_response_schema(self):
        for name in ['../escape','/tmp/escape','.env','.git/config']:
            with self.assertRaises(ValueError): safe_path(self.repo,name)
        schema=response_schema(RepairDecision)
        self.assertNotIn('additionalProperties',json.dumps(schema))

    def test_false_positive_must_not_be_an_empty_fix_proposal(self):
        from pydantic import ValidationError
        with self.assertRaisesRegex(ValidationError,'decision=not_a_bug'):
            RepairDecision(decision='propose_fix',reasoning='This is normal behavior',paths=[],edits=[])

    def test_diagnostic_test_can_disprove_a_report_without_source_changes(self):
        proposal=self.proposal()
        proposal.decision='propose_test'
        proposal.edits=[FileEdit(path='tests/diagnostic.test.js',old_text='',new_text="import test from 'node:test';import assert from 'node:assert/strict';import {add} from '../src/math.js';test('current behavior',()=>assert.equal(add(2,3),-1));")]
        self.brain._request.return_value=proposal
        result=repair(self.repo,self.report,self.output,self.brain)
        self.assertEqual(result['status'],'not_reproduced')
        self.assertEqual(result['changes'],[])
        self.assertIn('a-b',(self.repo/'src'/'math.js').read_text())

    def test_time_budget_replaces_iteration_limit(self):
        read=RepairDecision(decision='read_files',reasoning='Inspect further',paths=['src/math.js'],edits=[])
        done=RepairDecision(decision='not_a_bug',reasoning='Investigation complete',paths=[],edits=[])
        self.brain._request.side_effect=[read]*12+[done]
        result=repair(self.repo,self.report,self.output,self.brain)
        self.assertEqual(result['status'],'not_a_bug')
        self.assertEqual(self.brain._request.call_count,13)
        self.assertEqual(result['time_budget_seconds'],120)
        self.assertIsNotNone(self.brain._request.call_args.kwargs['deadline'])

    def test_timeout_restores_unvalidated_edits(self):
        from unittest.mock import patch
        from agent import repair as module
        original=module.command
        def expire_on_new_test(args,cwd,timeout=60):
            if args[:2]==['node','--test'] and len(args)>2:
                raise DeadlineExceeded()
            return original(args,cwd,timeout)
        self.brain._request.return_value=self.proposal()
        with patch('agent.repair.command',side_effect=expire_on_new_test):
            result=repair(self.repo,self.report,self.output,self.brain)
        self.assertEqual(result['status'],'inconclusive')
        self.assertIn('time budget',result['summary'])
        self.assertFalse((self.output/'workspace'/'tests'/'add.test.js').exists())
        self.assertIn('a-b',(self.output/'workspace'/'src'/'math.js').read_text())
        self.assertFalse((self.output/'fix.patch').exists())
