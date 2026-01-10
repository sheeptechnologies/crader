import unittest
from unittest.mock import mock_open, patch

from crader.volume_manager.git_volume_manager import GitVolumeManager


class TestGitVolumeManager(unittest.TestCase):
    def setUp(self):
        # We need to mock os.makedirs to avoid creating real dirs in Setup
        with patch("os.makedirs"):
            self.vm = GitVolumeManager()

    @patch("crader.volume_manager.git_volume_manager.subprocess.run")
    @patch("crader.volume_manager.git_volume_manager.fcntl")
    @patch("builtins.open", new_callable=mock_open)
    @patch("os.path.exists")
    @patch("os.makedirs")
    def test_ensure_repo_updated_clone(self, mock_makedirs, mock_exists, mock_file, mock_fcntl, mock_subprocess):
        """Test cloning a new repository."""
        # Repo does NOT exist
        mock_exists.return_value = False

        self.vm.ensure_repo_updated("https://github.com/org/repo.git")

        # Verify clone command
        self.assertTrue(mock_subprocess.called)
        args, kwargs = mock_subprocess.call_args
        cmd = args[0]
        self.assertIn("clone", cmd)
        self.assertIn("--mirror", cmd)
        self.assertIn("https://github.com/org/repo.git", cmd)

    @patch("crader.volume_manager.git_volume_manager.subprocess.run")
    @patch("crader.volume_manager.git_volume_manager.fcntl")
    @patch("builtins.open", new_callable=mock_open)
    @patch("os.path.exists")
    def test_ensure_repo_updated_fetch(self, mock_exists, mock_file, mock_fcntl, mock_subprocess):
        """Test updating an existing repository."""
        # Repo exists
        mock_exists.return_value = True

        self.vm.ensure_repo_updated("https://github.com/org/repo.git")

        # Verify fetch command
        self.assertTrue(mock_subprocess.called)
        args, kwargs = mock_subprocess.call_args
        cmd = args[0]
        self.assertIn("fetch", cmd)
        self.assertIn("--all", cmd)

    @patch("crader.volume_manager.git_volume_manager.subprocess.run")
    def test_get_head_commit(self, mock_run):
        """Test retrieving HEAD commit hash."""
        mock_run.return_value.stdout = b"abcdef123456\n"

        commit = self.vm.get_head_commit("repo_url", "main")

        # If implementation returns bytes
        if isinstance(commit, bytes):
            self.assertEqual(commit.strip(), b"abcdef123456")
        else:
            self.assertEqual(commit.strip(), "abcdef123456")
