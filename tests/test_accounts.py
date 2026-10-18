"""Account config and Keychain isolation checks; no real authorization or Gmail access."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from contextlib import ExitStack
from app import load_config
from providers import gmail_service, SERVICE

class AccountConfig(unittest.TestCase):
    def load(self, settings):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.json'
            path.write_text(json.dumps(settings))
            return load_config(path)

    def test_legacy_config_keeps_default_connection(self):
        self.assertEqual(self.load({})['gmail_accounts'][0]['id'], 'default')

    def test_invalid_accounts(self):
        for accounts in ([], None, ['work'], [{'id': '../work', 'label': 'Work', 'email': 'work@example.com'}],
                         [{'id': 'work', 'label': 'Work', 'email': ''}],
                         [{'id': 'work', 'label': '', 'email': 'work@example.com'}],
                         [{'id': 'work', 'label': 'Work', 'email': 'invalid'}],
                         [{'id': 'default', 'label': 'Personal'}, {'id': 'default', 'label': 'Other'}]):
            with self.subTest(accounts=accounts), self.assertRaises(ValueError):
                self.load({'gmail_accounts': accounts})

    def test_named_accounts(self):
        accounts = [{'id': 'default', 'label': 'Personal', 'email': ''},
                    {'id': 'work', 'label': 'Work', 'email': 'work@example.com'}]
        self.assertEqual(self.load({'gmail_accounts': accounts})['gmail_accounts'], accounts)

class GmailAuthorization(unittest.TestCase):
    def mocks(self, stack):
        stack.enter_context(patch('providers.sys.platform', 'darwin'))
        stack.enter_context(patch('keyring.backends.macOS.Keyring'))
        stack.enter_context(patch('keyring.set_keyring'))
        read = stack.enter_context(patch('keyring.get_password', return_value='{}'))
        write = stack.enter_context(patch('keyring.set_password'))
        creds = stack.enter_context(patch('google.oauth2.credentials.Credentials.from_authorized_user_info')).return_value
        creds.expired = False; creds.valid = True; creds.to_json.return_value = 'mock-token'
        service = stack.enter_context(patch('googleapiclient.discovery.build')).return_value
        service.users.return_value.getProfile.return_value.execute.return_value = {'emailAddress': 'work@example.com'}
        return read, write, service

    def test_account_tokens_stay_separate_and_legacy_token_is_preserved(self):
        with ExitStack() as stack:
            read, write, _ = self.mocks(stack)
            gmail_service(Path('unused'), account_id='default')
            read.assert_called_with(SERVICE, 'oauth')
            write.assert_called_with(SERVICE, 'oauth', 'mock-token')
            gmail_service(Path('unused'), account_id='work', expected_email='WORK@example.com')
            read.assert_called_with(SERVICE, 'oauth:work')
            write.assert_called_with(SERVICE, 'oauth:work', 'mock-token')

    def test_wrong_google_account_is_rejected_before_token_is_saved(self):
        with ExitStack() as stack:
            _, write, service = self.mocks(stack)
            with self.assertRaisesRegex(RuntimeError, 'does not match'):
                gmail_service(Path('unused'), account_id='personal', expected_email='personal@example.com')
            write.assert_not_called()
            service.users.return_value.threads.assert_not_called()

    def test_missing_account_authorization_does_not_reuse_default_token(self):
        with ExitStack() as stack:
            read, write, _ = self.mocks(stack)
            read.return_value = None
            with self.assertRaisesRegex(RuntimeError, '--account work'):
                gmail_service(Path('unused'), account_id='work', expected_email='work@example.com')
            read.assert_called_once_with(SERVICE, 'oauth:work')
            write.assert_not_called()

if __name__ == '__main__': unittest.main()
