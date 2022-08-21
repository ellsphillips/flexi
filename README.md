# flexi

<p align="center">
<a href="https://pypi.org/project/flexi" target="_blank">
    <img src="https://img.shields.io/pypi/v/flexi?label=version&logo=python&logoColor=%23fff&color=306998" alt="PyPI - Version">
</a>

<a href="https://pypi.org/project/flexi" target="_blank">
    <img src="https://img.shields.io/pypi/pyversions/flexi.svg?logo=python&logoColor=%23fff&color=306998" alt="PyPI - Version">
</a>
</p>

A command line tool to help manage your time, flexibly.

## Installation

`flexi` is available through PyPI:

```bash
  pip install flexi
```

## Usage

Get started by initialising flexi with the `init` action

```bash
  flexi init
```

This gives you access to the `--clock` and `--status` flags:

```bash
  flexi --clock in
  # Successfully clocked in at 08:53 21/08/2022

  flexi --clock out
  # Successfully clocked in at 17:32 21/08/2022
  # Cumulative work time today: 8 hours	40 minutes
```

```bash
  flexi --status
  # You are currently clocked in as of 08:53 21/08/2022
```


## Customising behaviour and parameters
