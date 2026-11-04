import base64, json, os, subprocess, tempfile, unittest
from pathlib import Path
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta
from urllib.error import HTTPError
import monitor, persistence
os.environ.setdefault('GITHUB_REPOSITORY', 'test/repo')
os.environ.setdefault('GH_TOKEN', 'test')
import watchdog

class RecoveryTests(unittest.TestCase):
    def test_github_status_recovers_after_502(self):
        response = MagicMock()
        response.__enter__.return_value.status = 200
        response.__enter__.return_value.read.return_value = b'{"workflow_runs": []}'
        error = HTTPError('https://api.github.com/', 502, 'Bad Gateway', {}, None)
        with patch.object(watchdog, 'urlopen', side_effect=[error, response]) as send, patch.object(watchdog.time, 'sleep'):
            self.assertEqual(watchdog.request('https://api.github.com/test'), {'workflow_runs': []})
            self.assertEqual(send.call_count, 2)

    def test_github_persistent_failure_still_fails(self):
        error = HTTPError('https://api.github.com/', 502, 'Bad Gateway', {}, None)
        with patch.object(watchdog, 'urlopen', side_effect=error) as send, patch.object(watchdog.time, 'sleep'):
            with self.assertRaises(HTTPError): watchdog.request('https://api.github.com/test')
            self.assertEqual(send.call_count, 4)

    def test_github_does_not_repeat_mutations_or_permission_errors(self):
        for method, code in [('POST', 502), ('GET', 403)]:
            with self.subTest(method=method, code=code):
                error = HTTPError('https://api.github.com/', code, 'error', {}, None)
                with patch.object(watchdog, 'urlopen', side_effect=error) as send, patch.object(watchdog.time, 'sleep'):
                    with self.assertRaises(HTTPError): watchdog.request('https://api.github.com/test', method)
                    self.assertEqual(send.call_count, 1)

    def test_discord_retry(self):
        response = MagicMock()
        response.__enter__.return_value.status = 204
        error = HTTPError('https://discord.com/', 429, 'rate limit', {'Retry-After': '3'}, None)
        with patch.object(monitor, 'urlopen', side_effect=[error, response]) as send, patch.object(monitor.time, 'sleep') as sleep:
            monitor.notify('https://discord.com/api/webhooks/1/test', 'test')
        self.assertEqual(send.call_count, 2)
        sleep.assert_called_once_with(3)

    def test_pending_survives_failed_delivery(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(monitor, 'STATE', Path(tmp)/'state.json'), patch.object(monitor, 'save_state'):
            state = {'categories': [], 'products': {}, 'pending': {'product': 'short-lived drop'}, 'delivered': []}
            persistence.atomic_write(monitor.STATE, state)
            with patch.object(monitor, 'notify', side_effect=RuntimeError('offline')):
                with self.assertRaises(RuntimeError): monitor.deliver_pending('test')
            self.assertEqual(json.loads(monitor.STATE.read_text())['pending'], state['pending'])
            with patch.object(monitor, 'notify') as send:
                monitor.deliver_pending('test')
                monitor.deliver_pending('test')
                send.assert_called_once_with('test', 'short-lived drop')

    def test_detected_alert_saved_before_delivery(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(monitor, 'STATE', Path(tmp)/'state.json'), patch.dict(os.environ, {'DISCORD_WEBHOOK_URL': 'https://discord.com/api/webhooks/1/test'}), patch('sys.argv', ['monitor.py']):
            persistence.atomic_write(monitor.STATE, {'categories': ['old'], 'products': {}})
            page = MagicMock(); page.categories.return_value = {'old'}; page.products.return_value = {'new': 'NEW'}
            order = []
            with patch.object(monitor, 'get_page', return_value=page), patch.object(monitor, 'save_state', side_effect=lambda: order.append('saved')), patch.object(monitor, 'notify', side_effect=lambda *args: order.append('sent')):
                monitor.main()
            self.assertEqual(order[:2], ['saved', 'sent'])

    def test_real_git_rebase_preserves_code_and_outbox(self):
        original = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            def cmd(*args, cwd=None):
                return subprocess.run(['git', *args], cwd=cwd, check=True, capture_output=True, text=True)
            remote, a, b = [str(Path(tmp)/x) for x in ('remote.git','a','b')]
            cmd('init','--bare','--initial-branch=main',remote); cmd('clone',remote,a)
            for key, val in [('user.email','test@example.com'),('user.name','Test')]: cmd('config',key,val,cwd=a)
            persistence.atomic_write(Path(a)/'state.json', {'categories': [], 'products': {}, 'pending': {}, 'delivered': []})
            (Path(a)/'code.py').write_text('old')
            cmd('add','.',cwd=a); cmd('commit','-m','initial',cwd=a); cmd('push','origin','main',cwd=a)
            cmd('clone',remote,b)
            for key, val in [('user.email','test@example.com'),('user.name','Test')]: cmd('config',key,val,cwd=b)
            persistence.atomic_write(Path(b)/'state.json', {'categories': [], 'products': {'b':'B'}, 'pending': {'b':'B'}, 'delivered': ['a']})
            (Path(b)/'code.py').write_text('new')
            cmd('add','.',cwd=b); cmd('commit','-m','concurrent',cwd=b); cmd('push','origin','main',cwd=b)
            persistence.atomic_write(Path(a)/'state.json', {'categories': [], 'products': {'a':'A'}, 'pending': {'a':'A','c':'C'}, 'delivered': []})
            try:
                os.chdir(a); persistence.save_state()
                state = json.loads(Path('state.json').read_text())
                self.assertEqual(state['pending'], {'b':'B','c':'C'})
                self.assertEqual(state['delivered'], ['a'])
                self.assertEqual(Path('code.py').read_text(),'new')
                self.assertEqual(cmd('rev-parse','HEAD').stdout,cmd('rev-parse','origin/main').stdout)
            finally: os.chdir(original)

    def test_stale_run_cancelled_and_restarted(self):
        now = datetime.now(timezone.utc).replace(hour=19)
        with patch.object(watchdog,'datetime') as clock, patch.object(watchdog,'request') as request, patch.object(watchdog,'alert') as alert, patch.dict(os.environ, {'REFRESH_MONITOR':'false'}):
            clock.now.return_value=now; clock.fromisoformat.side_effect=datetime.fromisoformat
            health={'last_success_utc':(now-timedelta(hours=1)).isoformat()}
            request.side_effect=[{'content':base64.b64encode(json.dumps(health).encode()).decode()}, {'workflow_runs':[{'id':1,'status':'in_progress'}]}, {'jobs':[{'status':'in_progress','started_at':(now-timedelta(hours=2)).isoformat()}]}, {}, {}]
            watchdog.main()
            self.assertTrue(request.call_args_list[-2].args[0].endswith('/1/cancel'))
            self.assertTrue(request.call_args_list[-1].args[0].endswith('/dispatches'))
            alert.assert_called_once()

