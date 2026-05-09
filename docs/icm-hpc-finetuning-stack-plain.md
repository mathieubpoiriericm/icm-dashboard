# How We Train Our Custom Research Assistant: A Plain-English Guide

This is a companion to the technical document of the same name. Where
that one reads like an instruction manual for a fellow engineer, this
one is written for anyone — a funder, a journalist, a curious relative
— who wants to understand what we are doing and why, without having to
look up jargon. Each technical name is introduced in plain English
first; the actual product names appear in parentheses, and a complete
reference table sits at the end.

## The big picture

Our research group at the Paris Brain Institute (the Institut du
Cerveau, abbreviated **ICM**) studies a brain condition called
cerebral small vessel disease (often shortened to **cSVD**), which
affects the tiny blood vessels deep inside the brain. Part of our
work involves combing through tens of thousands of scientific papers
to pick out which genes have been linked to the disease. Doing this
by hand is slow and tedious, so we use artificial intelligence to
read the papers for us.

The artificial intelligence we use at first is a large, commercial
service called **Claude**, made by a company named **Anthropic** —
think of Claude as a brilliant freelance research assistant who
charges by the hour. Claude works well, but at scale it gets
expensive and we have to send each paper out to the company's
servers, which is not ideal for sensitive material.

So we are doing something clever: we use Claude to write down its
answers on a few thousand example papers, and then we use those
examples to train our own in-house assistant. Our in-house version
is a freely available model called **Gemma 4** (more precisely, the
thirty-one-billion-parameter variant, often written **Gemma 4 31B**),
made by Google. Once Gemma has studied enough of Claude's examples,
it can do the same job on its own — smaller, free to run, and living
on our own computers.

The technical word for this kind of training-by-example is
*fine-tuning*. We're not building an artificial intelligence from
scratch (that would take a fortune and years of work). We're taking
a smart, freely available one and putting it through specialty
training, much like a general practitioner doctor going through a
residency to become a brain specialist.

This document describes the computer setup we use to do the
fine-tuning.

## Where the work happens

A laptop cannot do this kind of training. The Gemma model contains
tens of billions of internal settings, and adjusting them requires a
machine in a different weight class entirely.

The Paris Brain Institute runs a shared computing facility — formally
called a **high-performance computing cluster**, or **HPC cluster** —
which is a room full of very powerful machines that researchers
across the institute can borrow. Picture a garage full of race cars:
you do not own one, but you can reserve time on one, drive it as
hard as you can during your slot, and return it for the next person.
There is a booking system, called **SLURM**, that decides whose turn
it is. (SLURM is short for *Simple Linux Utility for Resource
Management*, though almost no one expands the acronym in
conversation.) Within the cluster, the section reserved for
artificial-intelligence work is called the **gpu-ampere partition** —
named after the generation of specialized chips that sit inside it.

Each machine in this garage is, by household standards, absurdly
overpowered. It has:

- Two industrial main processors (the regular kind of computer chip,
  technically called a **CPU** for *central processing unit* — the
  specific model is the **Intel Xeon Gold 6330**), each many times
  more capable than the one in a typical office laptop.
- About a thousand gigabytes of working memory, where a laptop might
  have eight or sixteen.
- A scratch pad of ultra-fast temporary storage that lives in memory
  itself, large enough to fit several feature-length films.
- One specialized chip dedicated to artificial-intelligence math (a
  **GPU**, for *graphics processing unit*) — specifically the
  **NVIDIA A100** with eighty gigabytes of its own private high-speed
  memory.
- An operating system called **Rocky Linux**, a free workalike of
  the commercial Red Hat Enterprise Linux that academic clusters
  often use.

That last big-ticket item — the specialized chip — is the workhorse,
and it deserves a paragraph of its own.

## Why we need a special chip

Regular computer chips (CPUs) are like a small team of expert chefs:
each one is highly skilled and can handle complicated tasks one
after another. Artificial-intelligence math is not like that. It is
more like a banquet hall full of line cooks all making the same dish
at once: thousands of small, repetitive multiplications happening
side by side.

Specialized chips (GPUs) are built for exactly that kind of work.
They can perform tens of thousands of small calculations in parallel,
which is why training a modern language model on a CPU would take
years and on a GPU takes hours or days. The particular GPU we use
(NVIDIA's A100, eighty-gigabyte version) holds eighty gigabytes of
its own private high-speed memory, which turns out to be just barely
enough for the model we are tuning.

We use one of these chips per training job. We could ask for two,
but two chips do not actually train twice as fast — closer to
twice-minus-fifteen-percent — and they cost double the shared
resources. When we are running many small experiments to compare
settings, we would rather have ten experiments going on one chip
each than five going on two chips each. The math favors single-chip
jobs.

## The shared toolbox

Because the computing facility is shared by many research groups,
the staff who run it cannot install every possible piece of software
everyone might want. Instead, they keep a library of useful tools
that researchers can check out at the start of a job, like books
from a library, and return when finished. The system that manages
this checkout process is called **Lmod** (short for *Lua Modules*),
a standard tool on academic clusters.

At the start of every job we ask the facility to lend us four
things:

- A translator that lets ordinary computer instructions speak the
  specialized chip's private language. This is **CUDA**, a toolkit
  made by NVIDIA, the company that makes our chip. CUDA is short for
  *Compute Unified Device Architecture* — a name no one ever says
  aloud.
- A book of pre-written mathematical recipes that artificial-
  intelligence training uses constantly. This is **cuDNN**, also from
  NVIDIA — short for *CUDA Deep Neural Network library*. Without it,
  we would be reinventing the same arithmetic every time.
- A general-purpose code translator that turns human-written
  instructions into a form the computer can run. This is **GCC**, the
  *GNU Compiler Collection*, the standard such tool on Linux systems.
- The programming language our training scripts are written in,
  called **Python**.

Borrowing these in the right order matters. They depend on one
another the way a recipe depends on having pots and pans available
before you start cooking. Loading these four also pulls in three
quiet supporting tools (called **glibc**, **gcc-runtime**, and a
local outbound web proxy) that the cluster brings along
automatically — we do not have to ask for them.

## Building the workshop

Modern software is assembled from dozens of smaller building blocks,
each written by a different team somewhere in the world. A typical
project uses many such blocks, and they have to fit together
properly: mixing incompatible versions of two blocks is a classic
source of mysterious crashes.

We use a tool called **uv** that locks down every block to one exact
version, the way a careful recipe specifies "the blue bag of flour
from this particular brand" rather than just "flour." The full list
of blocks lives in a file called **pyproject.toml**, and the exact
locked versions are recorded in a file called **uv.lock**. When a
collaborator wants to set up the same workshop on another machine,
they run a single command (`uv sync`) and end up with an identical
replica down to the smallest detail.

We keep our workshop on a shared lab storage drive rather than in
our personal folders, because personal folders on the facility have
a size limit, and our workshop is bulky.

## What does the training, exactly

A handful of specialized building blocks do the heavy lifting during
training. In plain terms:

- **The base engine: PyTorch.** The piece of software that knows how
  to drive the specialized chip and that everything else is built on
  top of. PyTorch was originally developed by Meta (the company
  behind Facebook) and is now maintained as an open-source project.
- **The speed-tuning library: Unsloth.** A few skilled engineers have
  spent years rewriting common training operations to be much faster
  than the defaults. Using Unsloth makes our training run roughly
  twice as fast at no cost to quality. Unsloth provides a tool
  called `FastModel`, which is the specific loader we use for
  Gemma 4 — the older `FastLanguageModel` loader cannot handle
  Gemma 4 because Gemma 4 was originally taught to read text, look
  at pictures, and listen to audio all at once. With one setting
  flipped on (`finetune_language_layers=True`), Unsloth freezes the
  picture-and-audio parts of the model and only tunes the
  text-reading part, which is all we need.
- **The training-loop library: TRL.** Short for *Transformer
  Reinforcement Learning*, made by a company called **Hugging Face**
  that publishes much of the standard artificial-intelligence
  toolkit. TRL is the conductor: it hands examples to the model one
  by one, checks how wrong the model's answer was, and nudges the
  model's internal settings in a slightly better direction. The
  particular conductor it provides is called `SFTTrainer`
  (*Supervised Fine-Tuning Trainer*). TRL also handles a subtle but
  important detail through a helper named `train_on_responses_only`,
  which tells the model "study the assistant's responses, not the
  user's questions" so that it learns to *answer* well, rather than
  learning to mimic the questions.
- **The adapter-management library: PEFT.** Short for *Parameter-
  Efficient Fine-Tuning*, also from Hugging Face. PEFT keeps track of
  the small "adapter" layers we add to the model — see below.
- **The memory-shrinking library: bitsandbytes.** Models normally
  store each of their billions of internal numbers with high
  precision (fifteen decimal places, roughly). The bitsandbytes
  library quietly compresses them down to a much rougher
  representation called **NF4** (*Normal Float 4-bit*) that takes
  about a quarter the space, with almost no loss in quality. It is
  the difference between shipping a 4K movie and a streaming-quality
  copy: a fraction of the size, indistinguishable to the viewer.
  Without this trick, our model would not fit on the chip at all.
  bitsandbytes also provides a memory-savvy version of the
  optimizer (the part of training that decides how much to nudge
  each setting), called `adamw_8bit`.

A few supporting libraries round things out: **transformers** (the
core Hugging Face library that knows how to load and run language
models), **datasets** (Hugging Face's library for handling training
examples), **accelerate** (a Hugging Face helper that smooths out
multi-chip training), and **tokenizers** (the Hugging Face library
that splits text into the small pieces a model can actually digest).

The overall training method we use — combining four-bit compression
of the base model with small adapter layers on top — has its own
acronym: **QLoRA**, short for *Quantized Low-Rank Adaptation*. The
"LoRA" part stands for *Low-Rank Adaptation*, the technique of
sticking small extra layers onto a model and training only those,
rather than rewriting the whole model. The "Q" stands for
*Quantized*, meaning the base model is compressed. Combining the two
is what lets us fit a thirty-one-billion-parameter model into eighty
gigabytes of chip memory.

## The quirks we have to work around

A handful of peculiarities about this particular facility shape how
we launch jobs.

**The base engine arrives with its own utensils.** When we install
PyTorch, it brings along private copies of several NVIDIA tools (the
internal-communication library **NCCL**, the matrix-math library
**cuBLAS**, and others) that the cluster also offers system-wide.
The two sets are not always identical, and a mismatch can cause
crashes. So our launch script firmly tells the program — by setting
an environment variable called **LD_LIBRARY_PATH** — to use the
utensils that came with PyTorch, not the ones on the wall.

**No internet on the working machines.** For security, the training
machines cannot reach the public internet. Most artificial-
intelligence libraries assume they can quietly download models and
related files from a public repository called the **Hugging Face
Hub** when needed. Ours cannot. So we download everything we need
ahead of time onto our lab storage and flip three switches —
**HF_HUB_OFFLINE**, **TRANSFORMERS_OFFLINE**, and
**HF_DATASETS_OFFLINE** — that tell the libraries to work strictly
from local files.

**Internal-communication settings.** Multiple chips and machines
talk to one another through the NCCL library mentioned above (its
full name is *NVIDIA Collective Communications Library*). NCCL has
two relevant transport mechanisms: a fast peer-to-peer link between
two chips on the same machine called **NVLink**, and a high-speed
network protocol for talking to other machines called
**InfiniBand**. We turn off InfiniBand (we are not talking to other
machines) and leave NVLink on (in case we ever do a two-chip run).

## Why we set things up this way

Every notable choice has a reason behind it:

- **One A100 chip instead of two.** As mentioned earlier, two chips
  do not actually train twice as fast and they cost double. When we
  are running many small experiments to compare settings, the math
  favors several single-chip experiments running side by side rather
  than fewer two-chip experiments.
- **Unsloth instead of plain PyTorch + TRL.** A free doubling in
  speed is not something to refuse. The fact that Unsloth can load
  multimodal models like Gemma 4 — which the default loader cannot —
  is a bonus.
- **QLoRA (four-bit base + LoRA adapters) instead of full-precision
  training.** Our model is at the very edge of what fits on the
  chip. Without compression, it simply would not fit, and we would
  have to use a smaller, less capable base model.
- **uv instead of the older Python tool pip.** Reproducibility is a
  scientific value. With uv's lock file, anyone should be able to
  recreate our exact setup with one command, rather than spending
  half a day chasing down "what version did you use?"

## The short version

We have rented time on a high-performance computing cluster at the
Paris Brain Institute (ICM), loaded a carefully chosen toolbox onto
it (CUDA, cuDNN, GCC, Python), assembled a Python workshop locked to
exact versions with uv, and used a stack of artificial-intelligence
libraries (PyTorch, Unsloth, TRL, PEFT, bitsandbytes, plus Hugging
Face's transformers, datasets, accelerate, and tokenizers) to teach
Google's Gemma 4 model — using a memory-efficient training method
called QLoRA — to do the specialized job of extracting gene
information from cerebral small vessel disease research papers. The
result is a smaller, faster, free, in-house assistant that does the
work our group needs without sending data to outside services.

## The full stack, for reference

Plain-English name on the left, the technical name on the right, and
what the thing actually is in the third column.

| Plain-English name | Technical name | What it is |
| --- | --- | --- |
| The institute | Institut du Cerveau (ICM) | The Paris Brain Institute |
| The disease | Cerebral small vessel disease (cSVD) | The condition our group studies |
| The shared facility | High-performance computing cluster (HPC cluster) | The room of powerful shared machines |
| The booking system | SLURM | The job scheduler that allocates time |
| The AI section of the cluster | gpu-ampere partition | Where the AI-capable machines live |
| The operating system | Rocky Linux 8.8 | The OS running on the cluster |
| The main processor | Intel Xeon Gold 6330 (CPU) | The general-purpose chip — two per machine |
| The specialized chip | NVIDIA A100 80 GB (GPU) | The AI workhorse — one per training job |
| The library checkout system | Lmod | Lets us load tools on demand |
| Chip toolkit | CUDA 12.4 | NVIDIA's chip-programming layer |
| AI math recipes | cuDNN 9.8 | CUDA Deep Neural Network library |
| Code translator | GCC 12.4 | GNU Compiler Collection |
| Programming language | Python 3.12 | What our training scripts are written in |
| Workshop locker | uv 0.11 | Python dependency manager (with `uv.lock`) |
| Base engine | PyTorch 2.6 | Foundational deep-learning library |
| Speed-tuning library | Unsloth | Fast QLoRA training, supports multimodal Gemma 4 |
| Training-loop conductor | TRL 0.23 | Transformer Reinforcement Learning (Hugging Face) |
| The conductor's main tool | SFTTrainer | Supervised Fine-Tuning Trainer (inside TRL) |
| Adapter-management library | PEFT 0.19 | Parameter-Efficient Fine-Tuning (Hugging Face) |
| Memory-shrinking library | bitsandbytes 0.49 | Four-bit compression + 8-bit optimizer |
| Compression format | NF4 | Normal Float 4-bit |
| Memory-savvy optimizer | adamw_8bit | 8-bit version of the AdamW optimizer |
| Core model toolkit | transformers 5.5 | Hugging Face's main library |
| Training-data library | datasets 4.3 | Hugging Face's data loader |
| Multi-chip helper | accelerate 1.13 | Hugging Face's training launcher |
| Text-splitting library | tokenizers 0.22 | Hugging Face's text preprocessor |
| Internal-communication library | NCCL | NVIDIA Collective Communications Library |
| Fast in-machine chip link | NVLink | Connects two GPUs on the same node |
| Cross-machine network protocol | InfiniBand | High-speed network (we leave it off) |
| Library-search environment variable | LD_LIBRARY_PATH | Tells programs which libraries to load |
| Offline-mode switches | HF_HUB_OFFLINE / TRANSFORMERS_OFFLINE / HF_DATASETS_OFFLINE | Disable Hugging Face's network probes |
| Public model repository | Hugging Face Hub | Where Gemma is normally downloaded from |
| The training method overall | QLoRA | Quantized Low-Rank Adaptation |
| Adapter technique | LoRA | Low-Rank Adaptation |
| The model we tune | Google Gemma 4 31B | The student |
| The commercial AI we use to make examples | Anthropic Claude | The teacher (the "freelance assistant") |
