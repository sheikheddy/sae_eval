from nnsight import LanguageModel
from datasets import load_dataset
import json

import experiments.utils as utils
from dictionary_learning.buffer import ActivationBuffer
from dictionary_learning.evaluation import evaluate

DEBUGGING = False

if DEBUGGING:
    tracer_kwargs = dict(scan=True, validate=True)
else:
    tracer_kwargs = dict(scan=False, validate=False)

DEVICE = "cuda"

transcoder = False

if transcoder:
    io = "in_and_out"
else:
    io = "out"

llm_batch_size = 16
buffer_size = 512
context_length = 128
sae_batch_size = 16
n_inputs = 10000

submodule_trainers = {
    # 'resid_post_layer_3': {"trainer_ids" : list(range(10,12))},
    "resid_post_layer_4": {"trainer_ids": list(range(10, 12, 2))},
}

model_name_lookup = {"pythia70m": "EleutherAI/pythia-70m-deduped"}
dictionaries_path = "../dictionary_learning/dictionaries"

model_location = "pythia70m"
sweep_name = "_sweep0709"
model_name = model_name_lookup[model_location]
model = LanguageModel(model_name, device_map=DEVICE, dispatch=True)

ae_group_paths = utils.get_ae_group_paths(
    dictionaries_path, model_location, sweep_name, submodule_trainers
)
ae_paths = utils.get_ae_paths(ae_group_paths)

# pile_dataset = load_dataset("monology/pile-uncopyrighted", streaming=True)
pile_dataset = load_dataset("NeelNanda/pile-10k", streaming=False)

input_strings = []

for i, example in enumerate(pile_dataset["train"]["text"]):
    if i == n_inputs:
        break
    input_strings.append(example[:context_length])


for ae_path in ae_paths:
    submodule, dictionary, config = utils.load_dictionary(model, model_name, ae_path, DEVICE)

    activation_dim = config["trainer"]["activation_dim"]

    activation_buffer_data = iter(input_strings)

    activation_buffer = ActivationBuffer(
        activation_buffer_data,
        model,
        submodule,
        n_ctxs=buffer_size,
        ctx_len=context_length,
        refresh_batch_size=llm_batch_size,
        out_batch_size=sae_batch_size,
        io=io,
        d_submodule=activation_dim,
        device=DEVICE,
    )

    eval_results = evaluate(
        dictionary, activation_buffer, context_length, llm_batch_size, io=io, device=DEVICE
    )
    print(eval_results)

    output_filename = f"{ae_path}/eval_results.json"
    with open(output_filename, "w") as f:
        json.dump(eval_results, f)
