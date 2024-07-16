import torch
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


@torch.no_grad()
def eval_saes(
    model: LanguageModel,
    model_name: str,
    ae_paths: list[str],
    n_inputs: int,
    context_length: int,
    llm_batch_size: int,
    sae_batch_size: int,
    device: str,
    transcoder: bool = False,
) -> dict:

    buffer_size = min(512, n_inputs)

    if transcoder:
        io = "in_and_out"
    else:
        io = "out"

    pile_dataset = load_dataset("NeelNanda/pile-10k", streaming=False)

    input_strings = []

    for i, example in enumerate(pile_dataset["train"]["text"]):
        if i == n_inputs:
            break
        input_strings.append(example[:context_length])

    for ae_path in ae_paths:
        submodule, dictionary, config = utils.load_dictionary(model, model_name, ae_path, device)

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
            device=device,
        )

        eval_results = evaluate(
            dictionary, activation_buffer, context_length, llm_batch_size, io=io, device=device
        )

        hyperparameters = {
            # TODO: Add batching so n_inputs is actually n_inputs
            "n_inputs": sae_batch_size,
            "context_length": context_length,
        }
        eval_results["hyperparameters"] = hyperparameters

        print(eval_results)

        output_filename = f"{ae_path}/eval_results.json"
        with open(output_filename, "w") as f:
            json.dump(eval_results, f)

    # return the final eval_results for testing purposes
    return eval_results


if __name__ == "__main__":
    DEVICE = "cuda"

    llm_batch_size = 10  # Approx 16GB VRAM on pythia70m with 128 context length
    # TODO: Don't hardcode context length
    context_length = 128
    sae_batch_size = 20
    n_inputs = 10000

    submodule_trainers = {"resid_post_layer_3": {"trainer_ids": [0]}}

    dictionaries_path = "../dictionary_learning/dictionaries"

    model_location = "pythia70m"
    sweep_name = "_test_sae"
    model_name = utils.model_name_lookup[model_location]
    model = LanguageModel(model_name, device_map=DEVICE, dispatch=True)

    ae_group_paths = utils.get_ae_group_paths(
        dictionaries_path, model_location, sweep_name, submodule_trainers
    )
    ae_paths = utils.get_ae_paths(ae_group_paths)

    eval_results = eval_saes(
        model,
        model_name,
        ae_paths,
        n_inputs,
        context_length,
        llm_batch_size,
        sae_batch_size,
        DEVICE,
    )

    print(f"Final eval results: {eval_results}")
