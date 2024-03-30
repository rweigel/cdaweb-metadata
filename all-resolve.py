# Usage: python all-resolve.py
#
# Creates data/all-resolve.json, which contains all datasets, each with keys of
# _all_xml, _master, _spase, and _file_list
# _all_xml contains the content of the dataset node in all.xml as JSON.
# The values of the the other keys are the paths to the cached JSON files.

import os
import json
from datetime import timedelta

test_run = False
expire_after = None # Use, e.g., timedelta(days=1), to force cache expiration
                    # after one day independent of cache-related headers.

def omit(id):
  if id == 'AIM_CIPS_SCI_3A':
    return True
  if test_run:
    if id.startswith("AC_H0"):
      return False
    return True
  else:
    return False

max_workers = 4
allxml  = 'https://spdf.gsfc.nasa.gov/pub/catalogs/all.xml'
filews  = 'https://cdaweb.gsfc.nasa.gov/WS/cdasr/1/dataviews/sp_phys/datasets/'

try:
  # TODO: Create and use setup.py
  import requests_cache
  import xmltodict
except:
  print(os.popen('pip install xmltodict requests_cache').read())

import requests_cache
import xmltodict

base_dir = os.path.dirname(__file__)
all_file = os.path.join(base_dir, 'data/all-resolve.json')

def get(function, datasets, cache_dir):

  session = CachedSession(cache_dir)

  if max_workers == 1:
    for dataset in datasets:
      function(dataset, session, cache_dir)
  else:
    from concurrent.futures import ThreadPoolExecutor
    def call(dataset):
      function(dataset, session, cache_dir)
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
      pool.map(call, datasets)

def CachedSession(cdir):

  # https://requests-cache.readthedocs.io/en/stable/#settings
  # https://requests-cache.readthedocs.io/en/stable/user_guide/headers.html

  # Cache dir
  copts = {
    "use_cache_dir": True,          # Save files in the default user cache dir
    "cache_control": False,         # Use Cache-Control response headers for expiration, if available
    "expire_after": expire_after,   # Expire responses after one day if no cache control header
    "allowable_codes": [200],       # Cache responses with these status codes
    "stale_if_error": True,         # In case of request errors, use stale cache data if possible
    "backend": "filesystem",
    "serializer": "json"
  }

  return requests_cache.CachedSession(cdir, **copts)

def create_datasets():

  """
  Create a list of datasets; each element has content of dataset node in
  all.xml. An info and id node is also added.
  """

  cdir = os.path.join(os.path.dirname(__file__), 'data/cache/all')
  session = CachedSession(cdir)

  resp = session.get(allxml)
  allxml_text = resp.text

  all_dict = xmltodict.parse(allxml_text);

  datasets = []
  for dataset_allxml in all_dict['sites']['datasite'][0]['dataset']:

    id = dataset_allxml['@serviceprovider_ID']

    if omit(id):
      continue

    startDate = dataset_allxml['@timerange_start'].replace(' ', 'T') + 'Z';
    stopDate = dataset_allxml['@timerange_stop'].replace(' ', 'T') + 'Z';

    contact = ''
    if 'data_producer' in dataset_allxml:
      if '@name' in dataset_allxml['data_producer']:
        contact = dataset_allxml['data_producer']['@name']
      if '@affiliation' in dataset_allxml['data_producer']:
        contact = contact + " @ " + dataset_allxml['data_producer']['@affiliation']

    dataset = {
                'id': id,
                'info': {
                    'startDate': startDate,
                    'stopDate': stopDate,
                    'resourceURL': f'https://cdaweb.gsfc.nasa.gov/misc/Notes{id[0]}.html#' + id,
                    'contact': contact
                },
                '_allxml': dataset_allxml
    }

    if not 'mastercdf' in dataset_allxml:
      print('No mastercdf for ' + id)
      continue

    if not '@ID' in dataset_allxml['mastercdf']:
      print('No ID attribute for mastercdf for ' + id)
      continue

    datasets.append(dataset)

  return datasets

def add_master(datasets):

  """
  Add a _master key to each dataset containing JSON from master CDF
  """

  def get_master(dataset, session, cache_dir):

    mastercdf = dataset['_allxml']['mastercdf']['@ID']
    masterjson = mastercdf.replace('.cdf', '.json').replace('0MASTERS', '0JSONS')

    try:
      r = session.get(masterjson)
    except:
      print("Error getting " + masterjson)
      return

    dataset['_master_data'] = r.json()

    print(f'Read: (from cache={r.from_cache}) {masterjson}')

    dataset['_master'] = cache_dir + "/" + r.cache_key + ".json"

    files = list(dataset['_master_data'].keys())
    if len(files) > 1:
      print("Aborting. Expected only one file key in _master object for " + dataset['id'])
      exit(1)

  cache_dir = os.path.join(os.path.dirname(__file__), 'data/cache/masters')
  get(get_master, datasets, cache_dir)

def add_spase(datasets):

  """
  Add a _spase key to each dataset containing JSON from master CDF
  """

  def get_spase(dataset, session, cache_dir):

    def spase_url(dataset):

      if '_master_data' not in dataset:
        return None

      files = list(dataset['_master_data'].keys())
      if len(files) > 1:
        print("Expected only one file key in _master object.")
        exit(0)

      global_attributes = dataset['_master_data'][files[0]]['CDFglobalAttributes']
      for attribute in global_attributes:
        if 'spase_DatasetResourceID' in attribute:
          id = attribute['spase_DatasetResourceID'][0]['0']
          return id.replace('spase://', 'https://hpde.io/') + '.json';

    url = spase_url(dataset)
    del dataset['_master_data']

    if url is None:
      dataset['_spase'] = None
      return

    try:
      r = session.get(url)
    except:
      print(f"{dataset['id']}: Error getting '{url}'")
      return

    if r.status_code != 200:
      print(f"{dataset['id']}: HTTP status != 200 ({r.status_code}) when getting '{url}'")
      return

    print(f'Read: (from cache={r.from_cache}) {url}')

    dataset['_spase'] = cache_dir + "/" + r.cache_key + ".json"

  cache_dir = os.path.join(os.path.dirname(__file__), 'data/cache/spase')
  get(get_spase, datasets, cache_dir)

def add_file_list(datasets):

  def get_file_list(dataset, session, cache_dir):

    start = dataset["info"]["startDate"].replace("-","").replace(":","")
    stop = dataset["info"]["stopDate"].replace("-","").replace(":","")
    url = filews + dataset["id"] + "/orig_data/" + start + "," + stop
    print("Requesting: " + url)
    r = session.get(url, headers={'Accept': 'application/json', 'Content-Type': 'application/json'})
    print(f'Read: (from cache={r.from_cache}) {url}')

    dataset['_file_list'] = cache_dir + "/" + r.cache_key + ".json"

  cache_dir = os.path.join(os.path.dirname(__file__), 'data/cache/files')
  get(get_file_list, datasets, cache_dir)

datasets = create_datasets()
add_master(datasets)
add_spase(datasets)
add_file_list(datasets)

print(f'# of datasets: {len(datasets)}')

print(f'Writing {all_file}')
with open(all_file, 'w', encoding='utf-8') as f:
  json.dump(datasets, f, indent=2)
print(f'Wrote {all_file}')