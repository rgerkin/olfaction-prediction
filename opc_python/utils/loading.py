import os
import csv
import json

import numpy as np
import pandas as pd

from .__init__ import *
import opc_python
from opc_python import *  # Import constants.
ROOT_PATH = os.path.split(opc_python.__path__[0])[0]
from opc_python.utils import search

DATA_PATH = os.path.join(ROOT_PATH, 'data')
PREDICTION_PATH = os.path.join(ROOT_PATH, 'predictions')


def load_raw_bmc_data(nrows=None):
    """Load raw data from Keller and Vosshall, 2016 supplement."""
    path = os.path.join(DATA_PATH, '12868_2016_287_MOESM1_ESM.xlsx')
    df_raw = pd.read_excel(path, header=2, nrows=nrows)
    return df_raw


def format_bmc_data(df,  # The raw data frame returned by `load_raw_bmc_data`
                    only_dream_subjects=True,  # Whether to only keep DREAM subjects
                    only_dream_descriptors=True,  # Whether to only keep DREAM descriptors
                    only_dream_molecules=True):  # Whether to only keep DREAM molecules
    """Format raw data from the BMC paper to be usable for modeling"""
    # Remove leading and trailing white space from column names
    df.columns = df.columns.str.strip()

    # Get the raw DREAM descriptor list
    descriptors_raw = get_descriptors()
    # Get the publication-style descriptor names
    descriptors = get_descriptors(format=True)
    # Revise to the Keller and Vosshall descriptor names
    descriptors_raw[0] = 'HOW STRONG IS THE SMELL?'
    descriptors_raw[1] = 'HOW PLEASANT IS THE SMELL?'

    # Possibly include "Familiarity" as a descriptor
    if not only_dream_descriptors: 
        descriptors_raw.append('HOW FAMILIAR IS THE SMELL?')
        descriptors.append('Familiarity')

    # Possibly restrict subjects to those used in the DREAM challenge
    # Note that numeric subject IDs in the BMC paper and in the DREAM
    # challenge are not identical
    if only_dream_subjects:
        df['Subject'] = df['Subject # (DREAM challenge)'].fillna(0).astype(int)
        df = df[df['Subject'] > 0]
    else:
        df['Subject'] = df['Subject # (this study)'].astype(int)

    # Rename columns to match DREAM challenge
    df = df.rename(columns={'Odor dilution': 'Dilution'})
    df = df.rename(columns=dict(zip(descriptors_raw, descriptors)))

    # Fix CIDs for molecules that only have CAS registry numbers.
    # Geranylacetone didn't have a CID listed in the raw data
    # Isobutyl acetate had the wrong CAS number in the raw data
    df['CID'] = df['CID'].astype(str)\
        .str.replace('3796-70-1', '1549778')\
        .str.replace('109-19-0', '8038')\
        .astype(int)

    # Possibly keep only the 476 DREAM challenge molecules
    if only_dream_molecules:
        dream_CIDs = get_CIDs(['training', 'leaderboard', 'testset'])
        assert len(dream_CIDs) == 476
        df = df[df['CID'].isin(dream_CIDs)]

    # Keep only relevant columns
    df = df[['CID', 'Dilution', 'Subject'] + descriptors]

    # Fill NaN descriptors values with 0 if Intensity is not 0.  
    df = df.apply(lambda x: x.fillna(0) if x['Intensity'] > 0 else x, axis=1)

    # Make dilution values integer -log10 dilutions
    df['Dilution'] = df['Dilution'].apply(dilution2magnitude).astype(float)

    # Set index and set column axis name
    df = df.set_index(['CID', 'Dilution', 'Subject'])
    df.columns.name = 'Descriptor'

    # Identify replicates and add this information to the index
    df['Replicate'] = df.index.duplicated().astype(int)
    df = df.reset_index().set_index(
            ['CID', 'Dilution', 'Replicate', 'Subject'])
    if only_dream_subjects:
        # DREAM subjects replicates should be properly indexed now
        assert df.index.duplicated().sum() == 0

    # Rearrange dataframe to pivot subjects and descriptors
    df = df.unstack('Subject').stack('Descriptor')
    df = df.reorder_levels(['Descriptor', 'CID', 'Dilution', 'Replicate'])
    df = df.sort_index()

    return df


def load_perceptual_data(kind, just_headers=False, raw=False):
    if type(kind) is list:
        dfs = [load_perceptual_data(k, raw=raw) for k in kind]
        df = pd.concat(dfs)
        return df
    if kind in ['training', 'training-norep', 'replicated']:
        kind2 = 'TrainSet'
    elif kind == 'leaderboard':
        kind2 = 'LeaderboardSet'
    elif kind == 'testset':
        kind2 = 'TestSet'
    else:
        raise ValueError("No such kind: %s" % kind)

    if kind in ['training-norep', 'replicated']:
        training = load_perceptual_data('training')
        with_replicates = [x[1:3] for x in training.index if x[3] == 1]
    data = []
    file_path = os.path.join(DATA_PATH, '%s.txt' % kind2)
    with open(file_path) as f:
        reader = csv.reader(f, delimiter="\t")
        for line_num, line in enumerate(reader):
            if line_num > 0:
                line[0:6] = [x.strip() for x in line[0:6]]
                line[2] = 1 if line[2]=='replicate' else 0
                line[6:] = [float('NaN') if x == 'NaN' else float(x) \
                            for x in line[6:]]
                line[0] = CID = int(line[0])
                dilution = line[4]
                mag = dilution2magnitude(dilution)
                CID_dilution = (CID, mag)
                if kind == 'training-norep':
                    if CID_dilution not in with_replicates:
                        data.append(line)
                elif kind == 'replicated':
                    if CID_dilution in with_replicates:
                        data.append(line)
                else:
                    if kind == 'leaderboard' or \
                       (kind == 'testset' and mag != -3):
                        rel_intensity = 'low' if line[3] == 'high' else 'high'
                        line[3] = rel_intensity
                        intensity = line[6]
                        line[6] = float('NaN')
                        data.append(line.copy())
                        line[6] = intensity
                        line[7:] = [float('NaN') for x in line[7:]]
                        line[3] = 'low' if rel_intensity == 'high' else 'high'
                        line[4] = "'1/1,000'"
                        data.append(line)
                    else:
                        data.append(line)
            else:
                headers = line
                if just_headers:
                    return headers
    df = pd.DataFrame(data, columns=headers)
    if not raw:
        df = format_perceptual_data(df)
    return df


def format_perceptual_data(perceptual_data, target_dilution=None,
                           use_replicates=True, subjects=range(1, 50)):
    p = perceptual_data
    p.rename(columns={'Compound Identifier': 'CID',
                      'Odor': 'Name',
                      'subject #': 'Subject'},inplace=True)
    p['Dilution'] = [dilution2magnitude(x) for x in p['Dilution']]
    p.set_index(['CID', 'Dilution', 'Replicate', 'Subject', 'Name'],
                inplace=True)
    p = p.unstack(['Subject'])
    descriptors = get_descriptors()
    dfs = [p[descriptor] for descriptor in descriptors]
    pd.options.mode.chained_assignment = None
    for i, desc in enumerate(descriptors):
        dfs[i]['Descriptor'] = \
            desc.split('/')[1 if desc.startswith('VAL') else 0].title()
        dfs[i].set_index('Descriptor', append=True, inplace=True)
    df = pd.concat(dfs)
    df.rename(columns={x: int(x) for x in df.columns}, inplace=True)
    df = df[sorted(df.columns)]
    df.reset_index(level='Name', inplace=True)
    df.insert(1, 'Solvent', None)
    df = df.transpose()
    df.index.name = ''
    df = df.transpose()
    df.columns = [['Metadata']*2+['Subject']*49, df.columns]
    df = df.reorder_levels(['Descriptor', 'CID', 'Dilution', 'Replicate'])
    df = df.sort_index()  # Was df.sortlevel()
    descriptors = get_descriptors(format=True)
    # Sort descriptors in paper order
    df = df.T[descriptors].T
    df['Subject'] = df['Subject'].astype(float)
    return df


def get_descriptors(format=False):
    headers = load_perceptual_data('training', just_headers=True)
    desc = headers[6:]
    if format:
        desc = [desc[col].split('/')[1 if col == 1 else 0] for col in range(21)]
        desc = [desc[col][0] + desc[col][1:].lower() for col in range(21)]
    return desc


def preformat_perceptual_data(kind):
    """Get leaderboard and testset data into the same file format
    as training data"""

    if kind == 'leaderboard':
        target_name = 'LeaderboardSet'
        data_name = 'LBs1'
    elif kind == 'testset':
        target_name = 'TestSet'
        data_name = 'GS'
    else:
        raise Exception("Expected 'leaderboard' or 'testset'.")
    new_file_path = os.path.join(DATA_PATH, '%s.txt' % target_name)
    f_new = open(new_file_path, 'w')
    writer = csv.writer(f_new, delimiter="\t")
    training_file_path = os.path.join(DATA_PATH, 'TrainSet.txt')
    headers = list(pd.read_csv(training_file_path, sep='\t').columns)
    descriptors = headers[6:]
    writer.writerow(headers)
    dilutions_file_path = os.path.join(DATA_PATH, 'dilution_%s.txt' % kind)
    dilutions = pd.read_csv(dilutions_file_path, index_col=0, header=0,
                            names=['CID', 'Dilution'], sep='\t')
    lines_new = {}
    data_path = os.path.join(DATA_PATH, '%s.txt' % data_name)
    with open(data_path) as f:
        reader = csv.reader(f, delimiter="\t")
        for line_num, line in enumerate(reader):
            if line_num > 0:
                CID, subject, descriptor, value = line
                CID = int(CID)
                subject = int(subject)
                dilution = dilutions.loc[CID]['Dilution']
                if kind == 'testset' and dilution2magnitude(dilution) == -5:
                    dilution = "'1/1,000'"
                mag = dilution2magnitude(dilution)
                if descriptor == 'INTENSITY/STRENGTH':
                    if kind == 'testset':
                        high = True
                    else:
                        high = mag > -3
                else:
                    if kind == 'testset':
                        high = True
                    else:
                        high = mag > -3
                mag = dilution2magnitude(dilution)
                line_id = '%d_%d_%d' % (CID, subject, mag)
                if line_id not in lines_new:
                    print(line_id)
                    lines_new[line_id] = [CID, 'N/A', 0,
                                          'high' if high else 'low',
                                          dilution, subject] + ['NaN']*21
                lines_new[line_id][6+descriptors.index(descriptor.strip())] = \
                    value

    for line_id in sorted(lines_new,
                          key=lambda x:[int(_) for _ in x.split('_')]):
        line = lines_new[line_id]
        writer.writerow(line)
    f_new.close()


def make_nspdk_dict(CIDs):
    nspdk_CIDs = pd.read_csv('%s/derived/nspdk_cid.csv' % DATA_PATH,
                             header=None, dtype='int').values.squeeze()
    # Start to load the NSPDK features.
    with open('%s/derived/nspdk_r3_d4_unaug.svm' % DATA_PATH) as f:
        nspdk_dict = {}
        i = 0
        while True:
            x = f.readline()
            if not len(x):
                break
            CID = nspdk_CIDs[i]
            i += 1
            if CID in CIDs:
                key_vals = x.split(' ')[1:]
                for key_val in key_vals:
                    key, val = key_val.split(':')
                    key = int(key)
                    val = float(val)
                    if key in nspdk_dict:
                        nspdk_dict[key][CID] = val
                    else:
                        nspdk_dict[key] = {CID: val}
    # Only include NSPDK features known for more than one of our CIDs
    nspdk_dict = {key: value for key, value in nspdk_dict.items() if len(value) > 1}
    return nspdk_dict


def get_molecular_data(sources,CIDs):
    dfs = {}
    for source in sources:
        if source == 'dragon':
            mdd_file_path = os.path.join(DATA_PATH,
                                         'molecular_descriptors_data.txt')
            df = pd.read_csv(mdd_file_path, delimiter='\t', index_col=0)
            df = df.loc[CIDs,:]
        elif source == 'episuite':
            df = pd.read_csv('%s/DREAM_episuite_descriptors.txt' % DATA_PATH,
                             index_col=0, sep='\t').drop('SMILES', 1)
            df = df.loc[CIDs]
            df.iloc[:, 47] = 1*(df.iloc[:, 47] == 'YES ')
        elif source == 'morgan':
            df = pd.read_csv('%s/morgan_sim.csv' % DATA_PATH, index_col=0)
            df.index.rename('CID', inplace=True)
            df = df.loc[CIDs]
        elif source == 'nspdk':
            nspdk_dict = make_nspdk_dict(CIDs)
            df = pd.DataFrame.from_dict(nspdk_dict)
        elif source == 'gramian':
            nspdk_CIDs = pd.read_csv('%s/derived/nspdk_cid.csv' % DATA_PATH,
                                     header=None, dtype='int')\
                                     .values.squeeze()
            # These require a large file that is not on GitHub, but can be obtained separately.
            df = pd.read_csv('%s/derived/nspdk_r3_d4_unaug_gramian.mtx' \
                               % DATA_PATH, sep=' ', header=None)
            CID_indices = [list(nspdk_CIDs).index(CID) for CID in CIDs]
            df = df.loc[CID_indices,:]
            df.index = CIDs
        elif source == 'mordred':
            df = pd.read_csv('%s/mordred-features.csv' % DATA_PATH)
            df = df.set_index('CID')
            numeric_cols = df.dtypes[df.dtypes != 'object']
            df = df[list(numeric_cols.index)]
        else:
            raise Exception("Unknown source '%s'" % source)
        print("%s has %d features for %d molecules." % \
              (source.title(),df.shape[1],df.shape[0]))
        dfs[source] = df
    df = pd.concat(dfs,axis=1)
    df.index.name = 'CID'

    print("There are now %d total features." % (df.shape[1]))
    return df


def get_CID_dilutions(kind, target_dilution=None, cached=True):
    if type(kind) is list:
        data = []
        for k in kind:
            d = get_CID_dilutions(k, target_dilution=target_dilution,
                                     cached=cached)
            data.append(d)
        if len(data) == 1:
            data = data[0]
        else:
            data = pd.MultiIndex.append(*data)
        data = data.sort_values()
        return data
    assert kind in ['training', 'training-norep', 'replicated',
                    'leaderboard', 'testset']
    """Return CIDs for molecules that will be used for:
        'leaderboard': the leaderboard to determine the provisional
                       leaders of the competition.
        'testset': final testing to determine the winners
                   of the competition."""
    if cached:
        file_path = os.path.join(DATA_PATH, 'derived', '%s.csv' % kind)
        if not os.path.isfile(file_path):
            print(("Determining CIDs and dilutions the long way one time. "
                   "Results will be stored for faster retrieval in the future."))
            cache_cid_dilutions()
        data = pd.read_csv(file_path)
    else:  # Note this may not include some of the testset dilutions
        if kind in ['training', 'replicated', 'leaderboard', 'testset']:
            data = []
            perceptual_data = load_perceptual_data(kind)
            for i, row in perceptual_data.iterrows():
                replicate = row.name[3]
                if replicate or kind != 'replicated':
                    CID = row.name[1]
                    dilution = row.name[2]
                    dilutions = perceptual_data.loc['Intensity']\
                                               .loc[CID]\
                                               .index\
                                               .get_level_values('Dilution')
                    high = dilution == dilutions.max()
                    if target_dilution == 'high' and not high:
                        continue
                    if target_dilution == 'low' and high:
                        continue
                    elif target_dilution not in [None, 'high', 'low'] and \
                      dilution != target_dilution:
                        continue
                    data.append((CID,dilution))
                    if kind in ['leaderboard', 'testset']:
                        data.append((CID, -3.0))  # Add the Intensity dilution.
            data = list(set(data))
        elif kind == 'training-norep':
            training = set(get_CID_dilutions('training',
                                             target_dilution=target_dilution,
                                             cached=cached))
            replicated = set(get_CID_dilutions('replicated',
                                               target_dilution=target_dilution,
                                               cached=cached))
            data = list(training.difference(replicated))
        data = sorted(list(set(data)))
        data = pd.DataFrame(data, columns=['CID', 'Dilution'])
    data['CID'] = data['CID'].astype(int)
    data['Dilution'] = data['Dilution'].astype(float)
    data = pd.MultiIndex.from_frame(data).sort_values()
    return data


def get_CIDs(kind, target_dilution=None, cached=True):
    CID_dilutions = get_CID_dilutions(kind,
                                      target_dilution=target_dilution,
                                      cached=cached)
    CIDs = CID_dilutions.get_level_values('CID').sort_values().unique()
    return CIDs


def get_CID_rank(kind, dilution=-3):
    """Returns CID dictionary with 1 if -3 dilution is highest,
    0 if it is lowest, -1 if it is not present.
    """

    CID_dilutions = get_CID_dilutions(kind)
    CIDs = set([int(_.split('_')[0]) for _ in CID_dilutions])
    result = {}
    for CID in CIDs:
        high = '%d_%g_%d' % (CID, dilution, 1)
        low = '%d_%g_%d' % (CID, dilution, 0)
        if high in CID_dilutions:
            result[CID] = 1
        elif low in CID_dilutions:
            result[CID] = 0
        else:
            result[CID] = -1
    return result


def dilution2magnitude(dilution):
    denom = dilution.replace('"', '').replace("'", "")\
                    .split('/')[1].replace(',', '')
    return np.log10(1.0/float(denom))


def load_data_matrix(kind='training', gold_standard_only=False,
                     only_replicates=False):
    """
    Loads the data into a 6-dimensional matrix:
    Indices are:
     subject number (0-48)
     CID index
     descriptor number (0-20)
     dilution rank (1/10=0, 1/1000=1, 1/100000=2, 1/1000000=3)
     replicate (original=0, replicate=1)
    Data is masked so locations with no data are not included
    in statistics on this array.
    """

    _, perceptual_obs_data = load_perceptual_data(kind)
    CIDs = get_CIDs(kind)
    data = np.ma.zeros((49, len(CIDs), 21, 4, 2), dtype='float')
    data.mask += 1
    for line in perceptual_obs_data:
        CID_index = CIDs.index(int(line[0]))
        subject = int(line[5])
        is_replicate = line[2]
        dilution_index = ['1/10','1/1,000','1/100,000','1/10,000,000']\
                         .index(line[4])
        for i,value in enumerate(line[6:]):
            indices = subject-1, CID_index, i, dilution_index, int(is_replicate)
            if value != 'NaN':
                if gold_standard_only:
                    if (i == 0 and dilution_index == 1) \
                      or (i > 0 and line[3] == 'high'):
                        data[indices] = float(value)
                else:
                    data[indices] = float()
    if only_replicates:
        only_replicates = data.copy()
        only_replicates.mask[:, :, :, :, 0] = (data.mask[:, :, :, :, 0] +
                                               data.mask[:, :, :, :, 1]) > 0
        only_replicates.mask[:, :, :, :, 1] = (data.mask[:, :, :, :, 0] +
                                               data.mask[:, :, :, :, 1]) > 0
        data = only_replicates
    return data


"""Output"""

# Write predictions for each subchallenge to a file.


def open_prediction_file(subchallenge, kind, name):
    prediction_file_path = os.path.join(PREDICTION_PATH,
                                        'challenge_%d_%s_%s.txt'
                                        % (subchallenge, kind, name))
    f = open(prediction_file_path, 'w')
    writer = csv.writer(f, delimiter='\t')
    return f, writer


def write_prediction_files(Y, kind, subchallenge, name):
    f, writer = open_prediction_file(subchallenge, kind, name=name)
    CIDs = get_CIDs(kind)
    descriptors_short = get_descriptors(format=True)
    descriptors_long = get_descriptors()

    # Subchallenge 1.
    if subchallenge == 1:
        writer.writerow(["#oID", "individual", "descriptor", "value"])
        for subject in range(1, NUM_SUBJECTS+1):
            for d_short, d_long in zip(descriptors_short, descriptors_long):
                for CID in CIDs:
                    value = Y[subject][d_short].loc[CID].round(3)
                    writer.writerow([CID, subject, d_long, value])
        f.close()

    # Subchallenge 2.
    elif subchallenge == 2:
        writer.writerow(["#oID", "descriptor", "value", "std"])
        for d_short, d_long in zip(descriptors_short, descriptors_long):
            for CID in CIDs:
                value = Y['mean'][d_short].loc[CID].round(3).round(3)
                std = Y['std'][d_short].loc[CID].round(3)
                writer.writerow([CID, d_long, value, std])
        f.close()


def load_eva_data(save_formatted=False):
    eva_file_path = os.path.join(DATA_PATH, 'eva_100_training_data.json')
    with open(eva_file_path) as f:
        eva_json = json.load(f)

    smile_cids = {}
    for smile in eva_json.keys():
        smile_cids[smile] = search.smile2cid(smile)

    cid_smiles = {cid: smile for smile, cid in smile_cids.items()}
    eva_cids = list(smile_cids.values)
    available_cids = []
    eva_data = []
    for kind in ('training', 'leaderboard', 'testset'):
        dream_cids = get_CIDs(kind)
        print(("Out of %d CIDs from the %s data, "
               "we have EVA data for %d of them."
               % (len(dream_cids), kind,
                  len(set(dream_cids).intersection(eva_cids)))))
    for cid in dream_cids:
        if cid in cid_smiles:
            available_cids.append(cid)

    available_cids = sorted(available_cids)
    for cid in available_cids:
        smile = cid_smiles[cid]
        eva_data.append(eva_json[smile])

    if save_formatted:
        np.savetxt(os.path.join(DATA_PATH, 'derived', 'eva_cids.dat'),
                   available_cids)
        np.savetxt(os.path.join(DATA_PATH, 'derived', 'eva_descriptors.dat'),
                   eva_data)

    return (available_cids, eva_data)


def pool_CID_dilutions(CID_dilution_list):
    CID_dilutions = pd.DataFrame(columns=['CID', 'Dilution'])
    for x in CID_dilution_list:
        CID_dilutions = CID_dilutions.append(x)
    CID_dilutions = CID_dilutions.sort_values(['CID', 'Dilution']).reset_index(drop=True)
    return CID_dilutions


def load_bmc_perceptual_data():
    print("Loading perceptual data")
    raw_perceptual_data = load_raw_bmc_data()
    print("Fomatting perceptual data")
    perceptual_data = format_bmc_data(raw_perceptual_data,
                                      only_dream_subjects=True, # Whether to only keep DREAM subjects
                                      only_dream_descriptors=True, # Whether to only keep DREAM descriptors
                                      only_dream_molecules=True) # Whether to only keep DREAM molecules)
    return perceptual_data


def cache_cid_dilutions():
    for kind in ['training', 'leaderboard', 'testset',
                 'training-norep', 'replicated']:
        CID_dilutions = get_CID_dilutions(kind, cached=False)
        derived = os.path.join(DATA_PATH, 'derived')
        os.makedirs(derived, exist_ok=True)
        path = os.path.join(derived, '%s.csv' % kind)
        CID_dilutions = CID_dilutions.to_frame(index=False)
        CID_dilutions.to_csv(path, header=True, index=False)
