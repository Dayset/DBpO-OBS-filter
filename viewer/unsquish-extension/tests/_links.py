"""Symlink a file into a test folder; copy it where symlinks need admin rights (Windows without Developer Mode)."""
import os
import shutil


def link(src, dst):
    try:
        os.symlink(src, dst)
    except OSError:
        shutil.copyfile(src, dst)
