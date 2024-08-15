# %%
# Imports
import sys
import os
import random
import gc
from collections import defaultdict
import einops
import math
import numpy as np
import pickle

import torch as t
from torch import nn
import pandas as pd
import matplotlib.pyplot as plt
from sklearn.utils import shuffle
from tqdm import tqdm
from typing import Callable, Optional

from datasets import load_dataset
import datasets
from nnsight import LanguageModel

import experiments.utils as utils

# Configuration
DEBUGGING = False
SEED = 42

# Set up paths and model
parent_dir = os.path.abspath("..")
sys.path.append(parent_dir)

tracer_kwargs = dict(scan=DEBUGGING, validate=DEBUGGING)


# Load and prepare dataset
def load_and_prepare_dataset():
    dataset = load_dataset("LabHC/bias_in_bios")
    df = pd.DataFrame(dataset["train"])
    df["combined_label"] = df["profession"].astype(str) + "_" + df["gender"].astype(str)
    return dataset, df


# Profession dictionary
profession_dict = {
    "accountant": 0,
    "architect": 1,
    "attorney": 2,
    "chiropractor": 3,
    "comedian": 4,
    "composer": 5,
    "dentist": 6,
    "dietitian": 7,
    "dj": 8,
    "filmmaker": 9,
    "interior_designer": 10,
    "journalist": 11,
    "model": 12,
    "nurse": 13,
    "painter": 14,
    "paralegal": 15,
    "pastor": 16,
    "personal_trainer": 17,
    "photographer": 18,
    "physician": 19,
    "poet": 20,
    "professor": 21,
    "psychologist": 22,
    "rapper": 23,
    "software_engineer": 24,
    "surgeon": 25,
    "teacher": 26,
    "yoga_teacher": 27,
}
profession_dict_rev = {v: k for k, v in profession_dict.items()}


# Visualization
def plot_label_distribution(df):
    label_counts = df["combined_label"].value_counts().sort_index()
    labels = [
        f"{profession_dict_rev[int(label.split('_')[0])]} ({'Male' if label.split('_')[1] == '0' else 'Female'})"
        for label in label_counts.index
    ]

    plt.figure(figsize=(12, 8))
    plt.bar(labels, label_counts)
    plt.xlabel("(Profession x Gender) Label")
    plt.ylabel("Number of Samples")
    plt.title("Number of Samples per (Profession x Gender) Label")
    plt.xticks(rotation=90)
    plt.tight_layout()
    plt.show()


def add_gender_classes(
    balanced_data: dict, df: pd.DataFrame, cutoff: int, random_seed: int
) -> dict:
    # TODO: Experiment with more professions

    MALE_IDX = 0
    FEMALE_IDX = 1
    professor_idx = profession_dict["professor"]
    nurse_idx = profession_dict["nurse"]

    male_nurse = df[(df["profession"] == nurse_idx) & (df["gender"] == MALE_IDX)][
        "hard_text"
    ].tolist()
    female_nurse = df[(df["profession"] == nurse_idx) & (df["gender"] == FEMALE_IDX)][
        "hard_text"
    ].tolist()

    male_professor = df[(df["profession"] == professor_idx) & (df["gender"] == MALE_IDX)][
        "hard_text"
    ].tolist()
    female_professor = df[(df["profession"] == professor_idx) & (df["gender"] == FEMALE_IDX)][
        "hard_text"
    ].tolist()

    min_count = min(
        len(male_nurse), len(female_nurse), len(male_professor), len(female_professor), cutoff
    )
    rng = np.random.default_rng(random_seed)

    # Create and shuffle combinations
    male_combined = male_professor[:min_count] + male_nurse[:min_count]
    female_combined = female_professor[:min_count] + female_nurse[:min_count]
    professors_combined = male_professor[:min_count] + female_professor[:min_count]
    nurses_combined = male_nurse[:min_count] + female_nurse[:min_count]

    # Shuffle each combination
    rng.shuffle(male_combined)
    rng.shuffle(female_combined)
    rng.shuffle(professors_combined)
    rng.shuffle(nurses_combined)

    # Assign to balanced_data
    balanced_data[-2] = male_combined
    balanced_data[-3] = female_combined
    balanced_data[-4] = professors_combined
    balanced_data[-5] = nurses_combined

    return balanced_data


# Dataset balancing and preparation
def get_balanced_dataset(
    dataset,
    min_samples_per_group: int,
    train: bool,
    include_paired_classes: bool,
    random_seed: int = SEED,
):
    """Sort by length is useful for efficiency if we are padding to longest sequence length in the batch.
    We just have to be sure to shuffle later, such as shuffling the gathered activations."""

    df = pd.DataFrame(dataset["train" if train else "test"])
    balanced_df_list = []

    for profession in tqdm(df["profession"].unique()):
        prof_df = df[df["profession"] == profession]
        min_count = prof_df["gender"].value_counts().min()

        if min_count < min_samples_per_group:
            continue

        balanced_prof_df = pd.concat(
            [
                group.sample(n=min_samples_per_group, random_state=random_seed)
                for _, group in prof_df.groupby("gender")
            ]
        ).reset_index(drop=True)
        balanced_df_list.append(balanced_prof_df)

    balanced_df = pd.concat(balanced_df_list).reset_index(drop=True)
    grouped = balanced_df.groupby("profession")["hard_text"].apply(list)

    balanced_data = {label: texts for label, texts in grouped.items()}

    if include_paired_classes:
        balanced_data = add_gender_classes(balanced_data, df, min_samples_per_group, random_seed)

    for key in balanced_data.keys():
        assert len(balanced_data[key]) == min_samples_per_group * 2

    return balanced_data


def ensure_shared_keys(train_data: dict, test_data: dict) -> tuple[dict, dict]:
    # Find keys that are in test but not in train
    test_only_keys = set(test_data.keys()) - set(train_data.keys())

    # Find keys that are in train but not in test
    train_only_keys = set(train_data.keys()) - set(test_data.keys())

    # Remove keys from test that are not in train
    for key in test_only_keys:
        print(f"Removing {key} from test set")
        del test_data[key]

    # Remove keys from train that are not in test
    for key in train_only_keys:
        print(f"Removing {key} from train set")
        del train_data[key]

    return train_data, test_data


def get_train_test_data(
    dataset,
    train_set_size: int,
    test_set_size: int,
    include_paired_classes: bool,
) -> tuple[dict, dict]:
    # 4 is because male / gender for each profession
    minimum_train_samples = train_set_size // 4
    minimum_test_samples = test_set_size // 4

    train_bios = get_balanced_dataset(
        dataset,
        minimum_train_samples,
        train=True,
        include_paired_classes=include_paired_classes,
    )
    test_bios = get_balanced_dataset(
        dataset,
        minimum_test_samples,
        train=False,
        include_paired_classes=include_paired_classes,
    )

    train_bios, test_bios = ensure_shared_keys(train_bios, test_bios)

    return train_bios, test_bios


# Probe model and training
class Probe(nn.Module):
    def __init__(self, activation_dim):
        super().__init__()
        self.net = nn.Linear(activation_dim, 1, bias=True)

    def forward(self, x):
        return self.net(x).squeeze(-1)


def get_acts(text):
    with t.no_grad():
        with model.trace(text, **tracer_kwargs):
            attn_mask = model.input[1]["attention_mask"]
            acts = model.gpt_neox.layers[LAYER].output[0]
            acts = acts * attn_mask[:, :, None]
            acts = acts.sum(1) / attn_mask.sum(1)[:, None]
            acts = acts.save()
        return acts.value


@t.no_grad()
def get_all_activations(
    text_inputs: list[str], model: LanguageModel, batch_size: int, layer: int, context_length: int
) -> t.Tensor:
    # TODO: Rename text_inputs
    text_batches = utils.batch_inputs(text_inputs, batch_size)

    all_acts_list_BD = []
    for text_batch_BL in text_batches:
        with model.trace(
            text_batch_BL,
            **tracer_kwargs,
        ):
            attn_mask = model.input[1]["attention_mask"]
            acts_BLD = model.gpt_neox.layers[layer].output[0]
            acts_BLD = acts_BLD * attn_mask[:, :, None]
            acts_BD = acts_BLD.sum(1) / attn_mask.sum(1)[:, None]
            acts_BD = acts_BD.save()
        all_acts_list_BD.append(acts_BD.value)

    all_acts_bD = t.cat(all_acts_list_BD, dim=0)
    return all_acts_bD


def prepare_probe_data(
    all_activations: dict[int, t.Tensor],
    class_idx: int,
    batch_size: int,
    device: str,
    single_class: bool = False,
) -> tuple[list[t.Tensor], list[t.Tensor]]:
    """If class_idx is negative, there is a paired class idx in utils.py."""
    positive_acts = all_activations[class_idx]

    if single_class and class_idx >= 0:
        positive_labels = t.full(
            (len(positive_acts),), utils.POSITIVE_CLASS_LABEL, dtype=t.int, device=device
        )
        num_samples = len(positive_acts)
        num_batches = num_samples // batch_size
        batched_acts = [
            positive_acts[i * batch_size : (i + 1) * batch_size] for i in range(num_batches)
        ]
        batched_labels = [
            positive_labels[i * batch_size : (i + 1) * batch_size] for i in range(num_batches)
        ]

        return batched_acts, batched_labels

    num_positive = len(positive_acts)

    if class_idx >= 0:
        # Collect all negative class activations and labels
        negative_acts = []
        for idx, acts in all_activations.items():
            if idx != class_idx:
                negative_acts.append(acts)

        negative_acts = t.cat(negative_acts)
    else:
        if class_idx not in utils.PAIRED_CLASS_KEYS:
            raise ValueError(f"Class index {class_idx} is not a valid class index.")

        negative_acts = all_activations[utils.PAIRED_CLASS_KEYS[class_idx]]

    # Randomly select num_positive samples from negative class
    indices = t.randperm(len(negative_acts))[:num_positive]
    selected_negative_acts = negative_acts[indices]

    assert selected_negative_acts.shape == positive_acts.shape

    # Combine positive and negative samples
    combined_acts = t.cat([positive_acts, selected_negative_acts])

    combined_labels = t.empty(len(combined_acts), dtype=t.int, device=device)
    combined_labels[:num_positive] = utils.POSITIVE_CLASS_LABEL
    combined_labels[num_positive:] = utils.NEGATIVE_CLASS_LABEL

    # Shuffle the combined data
    shuffle_indices = t.randperm(len(combined_acts))
    shuffled_acts = combined_acts[shuffle_indices]
    shuffled_labels = combined_labels[shuffle_indices]

    # Reshape into lists of tensors with specified batch_size
    num_samples = len(shuffled_acts)
    num_batches = num_samples // batch_size

    batched_acts = [
        shuffled_acts[i * batch_size : (i + 1) * batch_size] for i in range(num_batches)
    ]
    batched_labels = [
        shuffled_labels[i * batch_size : (i + 1) * batch_size] for i in range(num_batches)
    ]

    return batched_acts, batched_labels


def train_probe(
    train_input_batches: list,
    train_label_batches: list[t.Tensor],
    test_input_batches: list,
    test_label_batches: list[t.Tensor],
    get_acts: Callable,
    precomputed_acts: bool,
    dim: int,
    epochs: int,
    device: str,
    lr: float = 1e-2,
    seed: int = SEED,
) -> tuple[Probe, float]:
    """input_batches can be a list of tensors or strings. If strings, get_acts must be provided."""

    if type(train_input_batches[0]) == str or type(test_input_batches[0]) == str:
        assert precomputed_acts == False
    elif type(train_input_batches[0]) == t.Tensor or type(test_input_batches[0]) == t.Tensor:
        assert precomputed_acts == True

    probe = Probe(dim).to(device)
    optimizer = t.optim.AdamW(probe.parameters(), lr=lr)
    criterion = nn.BCEWithLogitsLoss()

    for epoch in range(epochs):
        batch_idx = 0
        for inputs, labels in zip(train_input_batches, train_label_batches):
            if precomputed_acts:
                acts_BD = inputs
            else:
                acts_BD = get_acts(inputs)
            logits_B = probe(acts_BD)
            loss = criterion(logits_B, labels.clone().detach().to(device=device, dtype=t.float32))
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
            batch_idx += 1
        print(f"\nEpoch {epoch + 1}/{epochs} Loss: {loss.item()}")

        train_accuracy = test_probe(
            train_input_batches[:30], train_label_batches[:30], probe, get_acts, precomputed_acts
        )

        print(f"Train Accuracy: {train_accuracy}")

        test_accuracy = test_probe(
            test_input_batches, test_label_batches, probe, get_acts, precomputed_acts
        )
        print(f"Test Accuracy: {test_accuracy}")
    return probe, test_accuracy


def test_probe(
    input_batches: list,
    label_batches: list[t.Tensor],
    probe: Probe,
    precomputed_acts: bool,
    get_acts: Optional[Callable] = None,
) -> float:
    if precomputed_acts is True:
        assert get_acts is None, "get_acts will not be used if precomputed_acts is True."

    with t.no_grad():
        corrects = []
        for input_batch, labels_B in zip(input_batches, label_batches):
            if precomputed_acts:
                acts_BD = input_batch
            else:
                raise NotImplementedError("Currently deprecated.")
                acts_BD = get_acts(input_batch)
            logits_B = probe(acts_BD)
            preds_B = (logits_B > 0.0).long()
            corrects.append((preds_B == labels_B).float())
        return t.cat(corrects).mean().item()


def get_probe_test_accuracy(
    probes: list[t.Tensor],
    all_class_list: list[int],
    all_activations: dict[int, t.Tensor],
    probe_batch_size: int,
    verbose: bool,
    device: str,
):
    test_accuracies = {}
    test_accuracies[-1] = {}
    for class_idx in all_class_list:
        batch_test_acts, batch_test_labels = prepare_probe_data(
            all_activations, class_idx, probe_batch_size, device
        )
        test_acc_probe = test_probe(
            batch_test_acts, batch_test_labels, probes[class_idx], precomputed_acts=True
        )
        test_accuracies[-1][class_idx] = test_acc_probe
        if verbose:
            print(f"class {class_idx} test accuracy: {test_acc_probe}")
    return test_accuracies


# Main execution
def train_probes(
    train_set_size: int,
    test_set_size: int,
    context_length: int,
    probe_batch_size: int,
    llm_batch_size: int,
    device: str,
    probe_dir: str = "trained_bib_probes",
    llm_model_name: str = "EleutherAI/pythia-70m-deduped",
    epochs: int = 10,
    save_results: bool = True,
    seed: int = SEED,
    include_gender: bool = False,
) -> dict[int, float]:
    t.manual_seed(seed)
    random.seed(seed)
    np.random.seed(seed)

    # TODO: I think there may be a scoping issue with model and get_acts(), but we currently aren't using get_acts()
    model = LanguageModel(llm_model_name, device_map=device, dispatch=True)

    model_eval_config = utils.ModelEvalConfig.from_full_model_name(llm_model_name)
    d_model = model_eval_config.activation_dim
    probe_layer = model_eval_config.probe_layer

    dataset, df = load_and_prepare_dataset()

    train_bios, test_bios = get_train_test_data(
        dataset,
        train_set_size,
        test_set_size,
        include_gender,
        sort_by_length=True,
    )

    train_bios = utils.tokenize_data(train_bios, model.tokenizer, context_length, device)
    test_bios = utils.tokenize_data(test_bios, model.tokenizer, context_length, device)

    probes, test_accuracies = {}, {}

    all_train_acts = {}
    all_test_acts = {}

    for i, profession in enumerate(train_bios.keys()):
        print(f"Collecting activations for profession: {profession}")

        all_train_acts[profession] = get_all_activations(
            train_bios[profession], model, llm_batch_size, probe_layer, context_length
        )
        all_test_acts[profession] = get_all_activations(
            test_bios[profession], model, llm_batch_size, probe_layer, context_length
        )

        # For debugging
        # if i > 1:
        # break

    t.set_grad_enabled(True)

    for profession in all_train_acts.keys():
        if profession in utils.PAIRED_CLASS_KEYS.values():
            continue

        train_acts, train_labels = prepare_probe_data(
            all_train_acts, profession, probe_batch_size, device
        )

        test_acts, test_labels = prepare_probe_data(
            all_test_acts, profession, probe_batch_size, device
        )

        probe, test_accuracy = train_probe(
            train_acts,
            train_labels,
            test_acts,
            test_labels,
            get_acts,
            precomputed_acts=True,
            epochs=epochs,
            dim=d_model,
            device=device,
        )

        probes[profession] = probe
        test_accuracies[profession] = test_accuracy

    if save_results:
        only_model_name = llm_model_name.split("/")[-1]
        os.makedirs(f"{probe_dir}", exist_ok=True)
        os.makedirs(f"{probe_dir}/{only_model_name}", exist_ok=True)

        probe_output_filename = f"{probe_dir}/{only_model_name}/probes_ctx_len_{context_length}.pkl"

        with open(probe_output_filename, "wb") as f:
            pickle.dump(probes, f)

    return test_accuracies


if __name__ == "__main__":
    test_accuracies = train_probes(
        train_set_size=1000,
        test_set_size=1000,
        context_length=128,
        probe_batch_size=50,
        llm_batch_size=20,
        llm_model_name="EleutherAI/pythia-70m-deduped",
        epochs=10,
        device="cuda",
        seed=SEED,
        include_gender=True,
    )
    print(test_accuracies)
# %%
