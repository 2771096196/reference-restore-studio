"""Portable local configuration; no personal paths are part of the source tree."""
import json
import os
from pathlib import Path

ROOT=Path(__file__).resolve().parent
config_file=Path(os.environ.get('RETOUCH_CONFIG_FILE',ROOT/'config.local.json')).expanduser()
LOCAL=json.loads(config_file.read_text(encoding='utf-8')) if config_file.is_file() else {}
if not isinstance(LOCAL,dict): raise ValueError('Local configuration must be a JSON object')


def path_setting(key,environment,default=None):
    value=os.environ.get(environment) or LOCAL.get(key) or default
    if not value:return None
    path=Path(value).expanduser()
    return (ROOT/path).resolve() if not path.is_absolute() else path.resolve()


DATA=path_setting('data_dir','RETOUCH_DATA_DIR','data')
WEIGHT_ROOT=path_setting('weights_dir','RETOUCH_WEIGHTS_DIR','models')
SAMPLE_IMAGE=path_setting('sample_image','RETOUCH_SAMPLE_IMAGE')
SAMPLE_VIDEO=path_setting('sample_video','RETOUCH_SAMPLE_VIDEO')
SAMPLE_PROJECT=str(LOCAL.get('sample_project','sample'))
if not SAMPLE_PROJECT or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in SAMPLE_PROJECT):
    raise ValueError('sample_project must be an alphanumeric project identifier')


def sample_available():
    return bool(SAMPLE_IMAGE and SAMPLE_VIDEO and SAMPLE_IMAGE.is_file() and SAMPLE_VIDEO.is_file())
