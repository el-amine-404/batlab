# The AI sync worker

Stanzarr needs no GPU. AI sync does: a **worker** on a computer with an NVIDIA
card takes the jobs, and Stanzarr shows AI sync as available only while a
worker with a GPU is online.

## What a job does

1. Downloads the song from Stanzarr.
2. Separates the voice from the music with **Demucs** (htdemucs).
3. Places every known line with three aligners, each in its own process so a
   4 GB card is enough:
   - **Whisper large-v3-turbo** and **Whisper large-v3** (faster-whisper, int8,
     through stable-ts), on the GPU;
   - **MMS** forced alignment (torchaudio), on the CPU.
4. Takes the **median** time per line. A line where the aligners disagree (no
   two within 0.5 s of the median, or more than 1.5 s apart) is marked unsure.
5. Sends back the lines and the separated voice (Ogg Opus), which the editor
   uses for its voice waveform and the "voice only" playback.

AI sync places words that are already known: it needs plain lyrics first.

## Accuracy

Measured on 8 songs a person had synced (English, French, Darija; slow and fast):

| Method | Lines within 0.5 s | Lines within 1 s |
|---|---|---|
| Whisper large-v3-turbo alone | 67% | 84% |
| Whisper large-v3 alone | 61% | 79% |
| MMS alone | 50% | 71% |
| **Vote of the three** | 66% | **86%** |

On the lines where the aligners agree (two thirds of all lines), the vote is
within 1 s **94%** of the time; where they disagree, 70%. That is why only
those lines are marked for checking.

## Requirements

- Linux, an NVIDIA GPU with 4 GB or more, a recent driver
- Python 3.10 to 3.12, ffmpeg
- About 8 GB of disk for the libraries and models

## Install and run

```sh
python3.12 -m venv ~/.stanzarr-worker
~/.stanzarr-worker/bin/pip install "stanzarr-worker @ git+https://github.com/el-amine-404/stanzarr.git#subdirectory=worker"
~/.stanzarr-worker/bin/stanzarr-worker --server http://your-stanzarr:6868 --key <key>
```

The key is in Stanzarr under **Workers**. It lets a worker download songs, so
keep it private; **New key** in the same page revokes the old one.

| Option | |
|---|---|
| `--name` | How the worker shows in Stanzarr (default: the hostname) |
| `--cpu` | Take jobs without a GPU (works, but takes many minutes per song) |
| `--once` | Do one job, then stop |

A worker that stops in the middle of a job is noticed after 5 minutes; the job
goes back to the queue.
