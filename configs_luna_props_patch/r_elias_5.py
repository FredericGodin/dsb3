#same as r_elias_4 but with mse

import numpy as np
import data_transforms
import data_iterators
import pathfinder
import lasagne as nn

from collections import OrderedDict, namedtuple
from functools import partial
import lasagne.layers.dnn as dnn
import lasagne
import theano.tensor as T
import utils

restart_from_save = False
rng = np.random.RandomState(33)

# transformations
p_transform = {'patch_size': (48, 48, 48),
               'mm_patch_size': (48, 48, 48),
               'pixel_spacing': (1., 1., 1.)
               }

p_transform_augment = {
    'translation_range_z': [-3, 3],
    'translation_range_y': [-3, 3],
    'translation_range_x': [-3, 3],
    'rotation_range_z': [-180, 180],
    'rotation_range_y': [-180, 180],
    'rotation_range_x': [-180, 180]
}


# data preparation function
def data_prep_function(data, patch_center, pixel_spacing, luna_origin, p_transform,
                       p_transform_augment, world_coord_system, **kwargs):
    x, patch_annotation_tf = data_transforms.transform_patch3d(data=data,
                                                               luna_annotations=None,
                                                               patch_center=patch_center,
                                                               p_transform=p_transform,
                                                               p_transform_augment=p_transform_augment,
                                                               pixel_spacing=pixel_spacing,
                                                               luna_origin=luna_origin,
                                                               world_coord_system=world_coord_system)
    x = data_transforms.hu2normHU(x)

    return x


data_prep_function_train = partial(data_prep_function, p_transform_augment=p_transform_augment,
                                   p_transform=p_transform, world_coord_system=True)
data_prep_function_valid = partial(data_prep_function, p_transform_augment=None,
                                   p_transform=p_transform, world_coord_system=True)

# data iterators
batch_size = 16
nbatches_chunk = 1
chunk_size = batch_size * nbatches_chunk

train_valid_ids = utils.load_pkl(pathfinder.LUNA_VALIDATION_SPLIT_PATH)
train_pids, valid_pids = train_valid_ids['train'], train_valid_ids['valid']


order_objectives = ['nodule', 
                    'size', 
                    'spiculation', 
                    'sphericity', 
                    'calcification',
                    'subtlety', 
                    'malignancy', 
                    'lobulation',
                    'texture',
                    'margin'] 

property_type = {'nodule': 'binary',
            'size': 'continuous',
            'spiculation': 'continuous',
            'sphericity': 'continuous',
            'calcification': 'continuous',
            'subtlety': 'continuous',
            'malignancy': 'continuous',
            'lobulation': 'continuous',
            'texture': 'continuous',
            'margin': 'continuous'}


train_data_iterator = data_iterators.CandidatesLunaPropsDataGenerator(data_path=pathfinder.LUNA_DATA_PATH,
                                                                 batch_size=chunk_size,
                                                                 transform_params=p_transform,
                                                                 data_prep_fun=data_prep_function_train,
                                                                 rng=rng,
                                                                 patient_ids=train_valid_ids['train'],
                                                                 full_batch=True, random=True, infinite=True,
                                                                 positive_proportion=0.5,
                                                                 order_objectives = order_objectives,
                                                                 return_enable_target_vector = True)

valid_data_iterator = data_iterators.CandidatesLunaPropsValidDataGenerator(data_path=pathfinder.LUNA_DATA_PATH,
                                                                      transform_params=p_transform,
                                                                      data_prep_fun=data_prep_function_valid,
                                                                      patient_ids=train_valid_ids['valid'],
                                                                      order_objectives = order_objectives,
                                                                      return_enable_target_vector = True)



nchunks_per_epoch = train_data_iterator.nsamples / chunk_size
max_nchunks = nchunks_per_epoch * 100

validate_every = int(5. * nchunks_per_epoch)
save_every = int(5. * nchunks_per_epoch)

learning_rate_schedule = {
    0: 4e-4,
    int(max_nchunks * 0.5): 1e-4,
    int(max_nchunks * 0.6): 5e-5,
    int(max_nchunks * 0.7): 2.5e-5,
    int(max_nchunks * 0.8): 1.25e-5,
    int(max_nchunks * 0.9): 0.625e-5
}

# model
conv3d = partial(dnn.Conv3DDNNLayer,
                 filter_size=3,
                 pad='same',
                 W=nn.init.Orthogonal(),
                 b=nn.init.Constant(0.01),
                 nonlinearity=nn.nonlinearities.very_leaky_rectify)

max_pool3d = partial(dnn.MaxPool3DDNNLayer,
                     pool_size=2)

drop = lasagne.layers.DropoutLayer

bn = lasagne.layers.batch_norm

dense = partial(lasagne.layers.DenseLayer,
                W=lasagne.init.Orthogonal('relu'),
                b=lasagne.init.Constant(0.0),
                nonlinearity=lasagne.nonlinearities.rectify)


def inrn_v2(lin):
    n_base_filter = 32

    l1 = conv3d(lin, n_base_filter, filter_size=1)

    l2 = conv3d(lin, n_base_filter, filter_size=1)
    l2 = conv3d(l2, n_base_filter, filter_size=3)

    l3 = conv3d(lin, n_base_filter, filter_size=1)
    l3 = conv3d(l3, n_base_filter, filter_size=3)
    l3 = conv3d(l3, n_base_filter, filter_size=3)

    l = lasagne.layers.ConcatLayer([l1, l2, l3])

    l = conv3d(l, lin.output_shape[1], filter_size=1)

    l = lasagne.layers.ElemwiseSumLayer([l, lin])

    l = lasagne.layers.NonlinearityLayer(l, nonlinearity=lasagne.nonlinearities.rectify)

    return l


def inrn_v2_red(lin):
    # We want to reduce our total volume /4

    den = 16
    nom2 = 4
    nom3 = 5
    nom4 = 7

    ins = lin.output_shape[1]

    l1 = max_pool3d(lin)

    l2 = conv3d(lin, ins // den * nom2, filter_size=3, stride=2)

    l3 = conv3d(lin, ins // den * nom2, filter_size=1)
    l3 = conv3d(l3, ins // den * nom3, filter_size=3, stride=2)

    l4 = conv3d(lin, ins // den * nom2, filter_size=1)
    l4 = conv3d(l4, ins // den * nom3, filter_size=3)
    l4 = conv3d(l4, ins // den * nom4, filter_size=3, stride=2)

    l = lasagne.layers.ConcatLayer([l1, l2, l3, l4])

    return l



def feat_red(lin):
    # We want to reduce the feature maps by a factor of 2
    ins = lin.output_shape[1]
    l = conv3d(lin, ins // 2, filter_size=1)
    return l

no_properties = len(order_objectives)

def build_model():
    l_in = nn.layers.InputLayer((None, ) + p_transform['patch_size'])
    l_ds = nn.layers.DimshuffleLayer(l_in, pattern=[0,'x',1,2,3])
    l_target = nn.layers.InputLayer((None, no_properties))
    l_enable_target = nn.layers.InputLayer((None, no_properties))

    l = conv3d(l_ds, 64)
    l = inrn_v2_red(l)
    l = inrn_v2(l)

    l = inrn_v2_red(l)
    l = inrn_v2(l)

    l = inrn_v2_red(l)
    l = inrn_v2_red(l)

    l = dense(drop(l), 512)

    final_layers = []
    unit_ptr = 0
    for obj_idx, obj_name in enumerate(order_objectives):
        ptype = property_type[obj_name]
        if ptype == 'binary':
            nonlin = nn.nonlinearities.sigmoid
        elif ptype == 'continuous':
            nonlin = nn.nonlinearities.softplus
        else:
            raise

        l_fin = nn.layers.DenseLayer(l, num_units=1,
                             W=lasagne.init.Orthogonal(),
                             b=lasagne.init.Constant(0.1),
                             nonlinearity=nonlin, name='dense_'+ptype+'_'+obj_name)

        final_layers.append(l_fin)

    l_out = nn.layers.ConcatLayer(final_layers, name = 'final_concat_layer')

    return namedtuple('Model', ['l_in', 'l_out', 'l_target', 'l_enable_target'])(l_in, l_out, l_target, l_enable_target)


d_objectives_deterministic = {} 
d_objectives = {}

def bce(target_idx, predictions, targets, epsilon):
    predictions = predictions[:,target_idx]
    predictions = T.cast(T.clip(predictions, epsilon, 1.-epsilon), 'float32')
    targets = T.cast(targets[:,target_idx], 'float32')
    out = nn.objectives.binary_crossentropy(predictions,targets)
    return out

def sqe(target_idx, predictions, targets):
    predictions = predictions[:,target_idx]
    targets = targets[:,target_idx]
    out = nn.objectives.squared_error(predictions,targets)
    return out


def build_objective(model, deterministic=False, epsilon=1e-12):
    predictions = nn.layers.get_output(model.l_out, deterministic=deterministic)
    targets = nn.layers.get_output(model.l_target)
    enable_targets = nn.layers.get_output(model.l_enable_target)
    

    sum_of_objectives = 0
    for obj_idx, obj_name in enumerate(order_objectives):
        ptype = property_type[obj_name]
        if ptype == 'binary':
            v_obj = bce(obj_idx, predictions, targets, epsilon)
        elif ptype == 'continuous':
            v_obj = sqe(obj_idx, predictions, targets)
        else:
            raise
        
        # take the mean of the objectives where it matters (enabled targets)
        obj_scalar =  T.sum(enable_targets[:,obj_idx] * v_obj) / (0.00001 + T.sum(enable_targets[:,obj_idx]))
        if deterministic:
            d_objectives_deterministic[obj_name] = obj_scalar
        else:
            d_objectives[obj_name] = obj_scalar

        sum_of_objectives += obj_scalar


    return sum_of_objectives


def build_updates(train_loss, model, learning_rate):
    updates = nn.updates.adam(train_loss, nn.layers.get_all_params(model.l_out, trainable=True), learning_rate)
    return updates
    
