# Audiobook-Manager

[![CI](https://github.com/TheBoscoClub/Audiobook-Manager/actions/workflows/ci.yml/badge.svg)](https://github.com/TheBoscoClub/Audiobook-Manager/actions/workflows/ci.yml) [![codecov](https://codecov.io/gh/TheBoscoClub/Audiobook-Manager/graph/badge.svg)](https://codecov.io/gh/TheBoscoClub/Audiobook-Manager) [![CodeFactor](https://www.codefactor.io/repository/github/theboscoclub/audiobook-manager/badge)](https://www.codefactor.io/repository/github/theboscoclub/audiobook-manager)

A comprehensive audiobook management toolkit for converting Audible files and browsing your audiobook collection.

> **A moment of silence for our ARMv7 friends.** To the brave souls still running 32-bit ARM stacks
> on your Raspberry Pi 2s, your ancient NAS boxes, and that one BeagleBone you swore you'd retire
> five years ago — I tried. I really did. I brought strong beer and sick weed to the altar of the
> Architecture Gods and offered them freely in exchange for `sqlcipher3` on armv7l. They took the
> offerings, laughed, and returned only `Conan build system does not support armv7l`. Your 32-bit
> spirits live on in our hearts, if not in our Docker manifests. Gone from `--platform`, never from
> memory. Pour one out. `linux/arm64` carries your legacy now.

## Version History

| Version | Status | Release |
|---------|--------|---------|
| ![8](https://img.shields.io/badge/8-brightgreen)![0](https://img.shields.io/badge/0-darkgreen)![2](https://img.shields.io/badge/2-green)![1](https://img.shields.io/badge/1-yellow) | Latest tweak | [v8.0.2.1](https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v8.0.2.1) |
| ![8](https://img.shields.io/badge/8-brightred)![0](https://img.shields.io/badge/0-darkred)![2](https://img.shields.io/badge/2-red) | Prior patch | [v8.0.2](https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v8.0.2) |
| ![8](https://img.shields.io/badge/8-brightred)![0](https://img.shields.io/badge/0-darkred)![1](https://img.shields.io/badge/1-red)![5](https://img.shields.io/badge/5-orange) | Prior tweak | [v8.0.1.5](https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v8.0.1.5) |
| ![8](https://img.shields.io/badge/8-brightred)![0](https://img.shields.io/badge/0-darkred)![1](https://img.shields.io/badge/1-red)![4](https://img.shields.io/badge/4-orange) | Prior tweak | [v8.0.1.4](https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v8.0.1.4) |
| ![8](https://img.shields.io/badge/8-brightred)![0](https://img.shields.io/badge/0-darkred)![1](https://img.shields.io/badge/1-red)![1](https://img.shields.io/badge/1-orange) | Prior tweak | [v8.0.1.1](https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v8.0.1.1) |
| ![8](https://img.shields.io/badge/8-brightred)![0](https://img.shields.io/badge/0-darkred)![1](https://img.shields.io/badge/1-red) | Prior patch | [v8.0.1](https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v8.0.1) |
| ![8](https://img.shields.io/badge/8-brightred)![0](https://img.shields.io/badge/0-darkred)![0](https://img.shields.io/badge/0-red) | Prior major | [v8.0.0](https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v8.0.0) |
| ![7](https://img.shields.io/badge/7-brightred)![6](https://img.shields.io/badge/6-darkred)![0](https://img.shields.io/badge/0-red) | Prior minor | [v7.6.0](https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v7.6.0) |
| ![7](https://img.shields.io/badge/7-brightred)![5](https://img.shields.io/badge/5-darkred)![3](https://img.shields.io/badge/3-red) | Prior patch | [v7.5.3](https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v7.5.3) |
| ![7](https://img.shields.io/badge/7-brightred)![5](https://img.shields.io/badge/5-darkred)![2](https://img.shields.io/badge/2-red)![1](https://img.shields.io/badge/1-orange) | Prior tweak | [v7.5.2.1](https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v7.5.2.1) |
| ![7](https://img.shields.io/badge/7-brightred)![5](https://img.shields.io/badge/5-darkred)![1](https://img.shields.io/badge/1-red)![3](https://img.shields.io/badge/3-orange) | Prior tweak | [v7.5.1.3](https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v7.5.1.3) |
| ![7](https://img.shields.io/badge/7-brightred)![5](https://img.shields.io/badge/5-darkred)![1](https://img.shields.io/badge/1-red)![2](https://img.shields.io/badge/2-orange) | Prior tweak | [v7.5.1.2](https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v7.5.1.2) |
| ![7](https://img.shields.io/badge/7-brightred)![5](https://img.shields.io/badge/5-darkred)![1](https://img.shields.io/badge/1-red)![1](https://img.shields.io/badge/1-orange) | Prior tweak | [v7.5.1.1](https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v7.5.1.1) |
| ![7](https://img.shields.io/badge/7-brightred)![5](https://img.shields.io/badge/5-darkred)![1](https://img.shields.io/badge/1-red) | Prior patch | [v7.5.1](https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v7.5.1) |
| ![7](https://img.shields.io/badge/7-brightred)![5](https://img.shields.io/badge/5-darkred)![0](https://img.shields.io/badge/0-red) | Prior minor | [v7.5.0](https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v7.5.0) |
| ![7](https://img.shields.io/badge/7-brightred)![4](https://img.shields.io/badge/4-darkred)![2](https://img.shields.io/badge/2-red) | Prior patch | [v7.4.2](https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v7.4.2) |
| ![7](https://img.shields.io/badge/7-brightred)![4](https://img.shields.io/badge/4-darkred)![1](https://img.shields.io/badge/1-red)![2](https://img.shields.io/badge/2-orange) | Prior tweak | [v7.4.1.2](https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v7.4.1.2) |
| ![7](https://img.shields.io/badge/7-brightred)![4](https://img.shields.io/badge/4-darkred)![1](https://img.shields.io/badge/1-red)![1](https://img.shields.io/badge/1-orange) | Prior tweak | [v7.4.1.1](https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v7.4.1.1) |
| ![7](https://img.shields.io/badge/7-brightred)![4](https://img.shields.io/badge/4-darkred)![1](https://img.shields.io/badge/1-red) | Prior patch | [v7.4.1](https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v7.4.1) |
| ![7](https://img.shields.io/badge/7-brightred)![4](https://img.shields.io/badge/4-darkred)![0](https://img.shields.io/badge/0-red) | Prior minor | [v7.4.0](https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v7.4.0) |
| ![7](https://img.shields.io/badge/7-brightred)![3](https://img.shields.io/badge/3-darkred)![0](https://img.shields.io/badge/0-red)![1](https://img.shields.io/badge/1-orange) | Prior tweak | [v7.3.0.1](https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v7.3.0.1) |
| ![7](https://img.shields.io/badge/7-brightred)![3](https://img.shields.io/badge/3-darkred)![0](https://img.shields.io/badge/0-red) | Prior minor | [v7.3.0](https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v7.3.0) |
| ![7](https://img.shields.io/badge/7-brightred)![2](https://img.shields.io/badge/2-darkred)![1](https://img.shields.io/badge/1-red)![1](https://img.shields.io/badge/1-orange) | Prior tweak | [v7.2.1.1](https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v7.2.1.1) |
| ![7](https://img.shields.io/badge/7-brightred)![2](https://img.shields.io/badge/2-darkred)![1](https://img.shields.io/badge/1-red) | Prior patch | [v7.2.1](https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v7.2.1) |
| ![7](https://img.shields.io/badge/7-brightred)![2](https://img.shields.io/badge/2-darkred)![0](https://img.shields.io/badge/0-red) | Prior minor | [v7.2.0](https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v7.2.0) |
| ![7](https://img.shields.io/badge/7-brightred)![1](https://img.shields.io/badge/1-darkred)![3](https://img.shields.io/badge/3-red)![4](https://img.shields.io/badge/4-orange) | Prior tweak | [v7.1.3.4](https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v7.1.3.4) |
| ![7](https://img.shields.io/badge/7-brightred)![1](https://img.shields.io/badge/1-darkred)![3](https://img.shields.io/badge/3-red)![3](https://img.shields.io/badge/3-orange) | Prior tweak | [v7.1.3.3](https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v7.1.3.3) |
| ![7](https://img.shields.io/badge/7-brightred)![1](https://img.shields.io/badge/1-darkred)![3](https://img.shields.io/badge/3-red)![2](https://img.shields.io/badge/2-orange) | Prior tweak | [v7.1.3.2](https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v7.1.3.2) |
| ![7](https://img.shields.io/badge/7-brightred)![1](https://img.shields.io/badge/1-darkred)![3](https://img.shields.io/badge/3-red)![1](https://img.shields.io/badge/1-orange) | Prior tweak | [v7.1.3.1](https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v7.1.3.1) |
| ![7](https://img.shields.io/badge/7-brightred)![1](https://img.shields.io/badge/1-darkred)![3](https://img.shields.io/badge/3-red) | Prior patch | [v7.1.3](https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v7.1.3) |
| ![7](https://img.shields.io/badge/7-brightred)![1](https://img.shields.io/badge/1-darkred)![2](https://img.shields.io/badge/2-red)![1](https://img.shields.io/badge/1-orange) | Prior tweak | [v7.1.2.1](https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v7.1.2.1) |
| ![7](https://img.shields.io/badge/7-brightred)![1](https://img.shields.io/badge/1-darkred)![2](https://img.shields.io/badge/2-red) | Prior patch | [v7.1.2](https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v7.1.2) |
| ![7](https://img.shields.io/badge/7-brightred)![1](https://img.shields.io/badge/1-darkred)![1](https://img.shields.io/badge/1-red)![1](https://img.shields.io/badge/1-orange) | Prior tweak | [v7.1.1.1](https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v7.1.1.1) |
| ![7](https://img.shields.io/badge/7-brightred)![1](https://img.shields.io/badge/1-darkred)![1](https://img.shields.io/badge/1-red) | Prior patch | [v7.1.1](https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v7.1.1) |
| ![7](https://img.shields.io/badge/7-brightred)![1](https://img.shields.io/badge/1-darkred)![0](https://img.shields.io/badge/0-red) | Prior minor | [v7.1.0](https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v7.1.0) |
| ![7](https://img.shields.io/badge/7-brightred)![0](https://img.shields.io/badge/0-darkred)![2](https://img.shields.io/badge/2-red) | Prior patch | [v7.0.2](https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v7.0.2) |
| ![7](https://img.shields.io/badge/7-brightred)![0](https://img.shields.io/badge/0-darkred)![1](https://img.shields.io/badge/1-red) | Prior patch | [v7.0.1](https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v7.0.1) |
| ![7](https://img.shields.io/badge/7-brightred)![0](https://img.shields.io/badge/0-darkred)![0](https://img.shields.io/badge/0-red) | Prior major | [v7.0.0](https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v7.0.0) |
| ![6](https://img.shields.io/badge/6-brightred)![7](https://img.shields.io/badge/7-darkred)![2](https://img.shields.io/badge/2-red)![4](https://img.shields.io/badge/4-orange) | Prior tweak | [v6.7.2.4](https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v6.7.2.4) |
| ![6](https://img.shields.io/badge/6-brightred)![7](https://img.shields.io/badge/7-darkred)![2](https://img.shields.io/badge/2-red)![3](https://img.shields.io/badge/3-orange) | Prior tweak | [v6.7.2.3](https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v6.7.2.3) |
| ![6](https://img.shields.io/badge/6-brightred)![7](https://img.shields.io/badge/7-darkred)![2](https://img.shields.io/badge/2-red) | Prior patch | [v6.7.2](https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v6.7.2) |
| ![6](https://img.shields.io/badge/6-brightred)![7](https://img.shields.io/badge/7-darkred)![1](https://img.shields.io/badge/1-red)![5](https://img.shields.io/badge/5-orange) | Prior tweak | [v6.7.1.5](https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v6.7.1.5) |
| ![6](https://img.shields.io/badge/6-brightred)![7](https://img.shields.io/badge/7-darkred)![1](https://img.shields.io/badge/1-red)![4](https://img.shields.io/badge/4-orange) | Prior tweak | [v6.7.1.4](https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v6.7.1.4) |
| ![6](https://img.shields.io/badge/6-brightred)![7](https://img.shields.io/badge/7-darkred)![1](https://img.shields.io/badge/1-red)![3](https://img.shields.io/badge/3-orange) | Prior tweak | [v6.7.1.3](https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v6.7.1.3) |
| ![6](https://img.shields.io/badge/6-brightred)![7](https://img.shields.io/badge/7-darkred)![1](https://img.shields.io/badge/1-red)![2](https://img.shields.io/badge/2-orange) | Prior tweak | [v6.7.1.2](https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v6.7.1.2) |
| ![6](https://img.shields.io/badge/6-brightred)![7](https://img.shields.io/badge/7-darkred)![1](https://img.shields.io/badge/1-red)![1](https://img.shields.io/badge/1-orange) | Prior tweak | [v6.7.1.1](https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v6.7.1.1) |
| ![6](https://img.shields.io/badge/6-brightred)![7](https://img.shields.io/badge/7-darkred)![1](https://img.shields.io/badge/1-red) | Prior patch | [v6.7.1](https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v6.7.1) |
| ![6](https://img.shields.io/badge/6-brightred)![7](https://img.shields.io/badge/7-darkred)![0](https://img.shields.io/badge/0-red)![3](https://img.shields.io/badge/3-orange) | Prior tweak | [v6.7.0.3](https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v6.7.0.3) |
| ![6](https://img.shields.io/badge/6-brightred)![7](https://img.shields.io/badge/7-darkred)![0](https://img.shields.io/badge/0-red)![2](https://img.shields.io/badge/2-orange) | Prior tweak | [v6.7.0.2](https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v6.7.0.2) |
| ![6](https://img.shields.io/badge/6-brightred)![7](https://img.shields.io/badge/7-darkred)![0](https://img.shields.io/badge/0-red)![1](https://img.shields.io/badge/1-orange) | Prior tweak | [v6.7.0.1](https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v6.7.0.1) |
| ![6](https://img.shields.io/badge/6-brightred)![7](https://img.shields.io/badge/7-darkred)![0](https://img.shields.io/badge/0-red) | Prior minor | [v6.7.0](https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v6.7.0) |
| ![6](https://img.shields.io/badge/6-brightred)![6](https://img.shields.io/badge/6-darkred)![7](https://img.shields.io/badge/7-red) | Prior patch | [v6.6.7](https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v6.6.7) |
| ![6](https://img.shields.io/badge/6-brightred)![6](https://img.shields.io/badge/6-darkred)![6](https://img.shields.io/badge/6-red)![1](https://img.shields.io/badge/1-orange) | Prior tweak | [v6.6.6.1](https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v6.6.6.1) |
| ![6](https://img.shields.io/badge/6-brightred)![6](https://img.shields.io/badge/6-darkred)![6](https://img.shields.io/badge/6-red) | Prior patch | [v6.6.6](https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v6.6.6) |
| ![6](https://img.shields.io/badge/6-brightred)![6](https://img.shields.io/badge/6-darkred)![5](https://img.shields.io/badge/5-red)![1](https://img.shields.io/badge/1-orange) | Prior tweak | [v6.6.5.1](https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v6.6.5.1) |
| ![6](https://img.shields.io/badge/6-brightred)![6](https://img.shields.io/badge/6-darkred)![5](https://img.shields.io/badge/5-red) | Prior patch | [v6.6.5](https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v6.6.5) |
| ![6](https://img.shields.io/badge/6-brightred)![6](https://img.shields.io/badge/6-darkred)![4](https://img.shields.io/badge/4-red) | Prior patch | [v6.6.4](https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v6.6.4) |
| ![6](https://img.shields.io/badge/6-brightred)![6](https://img.shields.io/badge/6-darkred)![3](https://img.shields.io/badge/3-red) | Prior patch | [v6.6.3](https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v6.6.3) |
| ![6](https://img.shields.io/badge/6-brightred)![6](https://img.shields.io/badge/6-darkred)![2](https://img.shields.io/badge/2-red)![6](https://img.shields.io/badge/6-orange) | Prior tweak | [v6.6.2.6](https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v6.6.2.6) |
| ![6](https://img.shields.io/badge/6-brightred)![6](https://img.shields.io/badge/6-darkred)![2](https://img.shields.io/badge/2-red)![5](https://img.shields.io/badge/5-orange) | Prior tweak | [v6.6.2.5](https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v6.6.2.5) |
| ![6](https://img.shields.io/badge/6-brightred)![6](https://img.shields.io/badge/6-darkred)![2](https://img.shields.io/badge/2-red)![4](https://img.shields.io/badge/4-orange) | Prior tweak | [v6.6.2.4](https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v6.6.2.4) |
| ![6](https://img.shields.io/badge/6-brightred)![6](https://img.shields.io/badge/6-darkred)![2](https://img.shields.io/badge/2-red)![3](https://img.shields.io/badge/3-orange) | Prior tweak | [v6.6.2.3](https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v6.6.2.3) |
| ![6](https://img.shields.io/badge/6-brightred)![6](https://img.shields.io/badge/6-darkred)![2](https://img.shields.io/badge/2-red)![2](https://img.shields.io/badge/2-orange) | Prior tweak | [v6.6.2.2](https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v6.6.2.2) |
| ![6](https://img.shields.io/badge/6-brightred)![6](https://img.shields.io/badge/6-darkred)![2](https://img.shields.io/badge/2-red)![1](https://img.shields.io/badge/1-orange) | Prior tweak | [v6.6.2.1](https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v6.6.2.1) |
| ![6](https://img.shields.io/badge/6-brightred)![6](https://img.shields.io/badge/6-darkred)![2](https://img.shields.io/badge/2-red) | Prior patch | [v6.6.2](https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v6.6.2) |
| ![6](https://img.shields.io/badge/6-brightred)![6](https://img.shields.io/badge/6-darkred)![1](https://img.shields.io/badge/1-red)![1](https://img.shields.io/badge/1-orange) | Prior tweak | [v6.6.1.1](https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v6.6.1.1) |
| ![6](https://img.shields.io/badge/6-brightred)![6](https://img.shields.io/badge/6-darkred)![1](https://img.shields.io/badge/1-red) | Prior patch | [v6.6.1](https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v6.6.1) |
| ![6](https://img.shields.io/badge/6-brightred)![6](https://img.shields.io/badge/6-darkred)![0](https://img.shields.io/badge/0-red) | Prior minor | [v6.6.0](https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v6.6.0) |
| ![6](https://img.shields.io/badge/6-brightred)![5](https://img.shields.io/badge/5-darkred)![0](https://img.shields.io/badge/0-red)![1](https://img.shields.io/badge/1-orange) | Prior tweak | [v6.5.0.1](https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v6.5.0.1) |
| ![6](https://img.shields.io/badge/6-brightred)![5](https://img.shields.io/badge/5-darkred)![0](https://img.shields.io/badge/0-red) | Prior minor | [v6.5.0](https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v6.5.0) |
| ![6](https://img.shields.io/badge/6-brightred)![4](https://img.shields.io/badge/4-darkred)![0](https://img.shields.io/badge/0-red)![1](https://img.shields.io/badge/1-orange) | Prior tweak | [v6.4.0.1](https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v6.4.0.1) |
| ![6](https://img.shields.io/badge/6-brightred)![4](https://img.shields.io/badge/4-darkred)![0](https://img.shields.io/badge/0-red) | Prior minor | [v6.4.0](https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v6.4.0) |
| ![6](https://img.shields.io/badge/6-brightred)![3](https://img.shields.io/badge/3-darkred)![0](https://img.shields.io/badge/0-red) | Prior minor | [v6.3.0](https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v6.3.0) |
| ![6](https://img.shields.io/badge/6-brightred)![2](https://img.shields.io/badge/2-darkred)![0](https://img.shields.io/badge/0-red)![1](https://img.shields.io/badge/1-orange) | Prior tweak | [v6.2.0.1](https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v6.2.0.1) |
| ![6](https://img.shields.io/badge/6-brightred)![2](https://img.shields.io/badge/2-darkred)![0](https://img.shields.io/badge/0-red) | Prior minor | [v6.2.0](https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v6.2.0) |
| ![6](https://img.shields.io/badge/6-brightred)![1](https://img.shields.io/badge/1-darkred)![3](https://img.shields.io/badge/3-red) | Prior patch | [v6.1.3](https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v6.1.3) |
| ![6](https://img.shields.io/badge/6-brightred)![1](https://img.shields.io/badge/1-darkred)![2](https://img.shields.io/badge/2-red)![1](https://img.shields.io/badge/1-orange) | Prior tweak | [v6.1.2.1](https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v6.1.2.1) |
| ![6](https://img.shields.io/badge/6-brightred)![1](https://img.shields.io/badge/1-darkred)![2](https://img.shields.io/badge/2-red) | Prior patch | [v6.1.2](https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v6.1.2) |
| ![6](https://img.shields.io/badge/6-brightred)![1](https://img.shields.io/badge/1-darkred)![1](https://img.shields.io/badge/1-red) | Prior patch | [v6.1.1](https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v6.1.1) |
| ![6](https://img.shields.io/badge/6-brightred)![1](https://img.shields.io/badge/1-darkred)![0](https://img.shields.io/badge/0-red) | Prior minor | [v6.1.0](https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v6.1.0) |
| ![6](https://img.shields.io/badge/6-brightred)![0](https://img.shields.io/badge/0-darkred)![0](https://img.shields.io/badge/0-red) | Prior major | [v6.0.0](https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v6.0.0) |
| ![5](https://img.shields.io/badge/5-brightred)![0](https://img.shields.io/badge/0-darkred)![2](https://img.shields.io/badge/2-red) | Prior patch | [v5.0.2](https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v5.0.2) |
| ![5](https://img.shields.io/badge/5-brightred)![0](https://img.shields.io/badge/0-darkred)![1](https://img.shields.io/badge/1-red)![1](https://img.shields.io/badge/1-orange) | Prior tweak | [v5.0.1.1](https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v5.0.1.1) |
| ![5](https://img.shields.io/badge/5-brightred)![0](https://img.shields.io/badge/0-darkred)![1](https://img.shields.io/badge/1-red) | Prior patch | [v5.0.1](https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v5.0.1) |
| ![5](https://img.shields.io/badge/5-brightred)![0](https://img.shields.io/badge/0-darkred)![0](https://img.shields.io/badge/0-red) | Prior major | [v5.0.0](https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v5.0.0) |
| ![4](https://img.shields.io/badge/4-brightred)![1](https://img.shields.io/badge/1-darkred)![2](https://img.shields.io/badge/2-red) | Prior patch | [v4.1.2](https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v4.1.2) |
| ![4](https://img.shields.io/badge/4-brightred)![1](https://img.shields.io/badge/1-darkred)![1](https://img.shields.io/badge/1-red) | Prior patch | [v4.1.1](https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v4.1.1) |
| ![4](https://img.shields.io/badge/4-brightred)![1](https://img.shields.io/badge/1-darkred)![0](https://img.shields.io/badge/0-red) | Prior minor | [v4.1.0](https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v4.1.0) |
| ![4](https://img.shields.io/badge/4-brightred)![0](https://img.shields.io/badge/0-darkred)![5](https://img.shields.io/badge/5-red) | Prior patch | [v4.0.5](https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v4.0.5) |
| ![4](https://img.shields.io/badge/4-brightred)![0](https://img.shields.io/badge/0-darkred)![1](https://img.shields.io/badge/1-red) | Prior patch | [v4.0.1](https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v4.0.1) |
| ![4](https://img.shields.io/badge/4-brightred)![0](https://img.shields.io/badge/0-darkred)![0](https://img.shields.io/badge/0-red)![2](https://img.shields.io/badge/2-orange) | Prior tweak | [v4.0.0.2](https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v4.0.0.2) |
| ![4](https://img.shields.io/badge/4-brightred)![0](https://img.shields.io/badge/0-darkred)![0](https://img.shields.io/badge/0-red)![1](https://img.shields.io/badge/1-orange) | Prior tweak | [v4.0.0.1](https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v4.0.0.1) |
| ![4](https://img.shields.io/badge/4-brightred)![0](https://img.shields.io/badge/0-darkred)![0](https://img.shields.io/badge/0-red) | Prior major | [v4.0.0](https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v4.0.0) |
| ![3](https://img.shields.io/badge/3-brightred)![11](https://img.shields.io/badge/11-darkred)![2](https://img.shields.io/badge/2-red) | Prior minor | [v3.11.2](https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v3.11.2) |
| ![3](https://img.shields.io/badge/3-brightred)![11](https://img.shields.io/badge/11-darkred)![1](https://img.shields.io/badge/1-red) | Prior patch | [v3.11.1](https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v3.11.1) |
| ![3](https://img.shields.io/badge/3-brightred)![11](https://img.shields.io/badge/11-darkred)![0](https://img.shields.io/badge/0-red) | Prior patch | [v3.11.0](https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v3.11.0) |
| ![3](https://img.shields.io/badge/3-brightred)![9](https://img.shields.io/badge/9-darkred)![8](https://img.shields.io/badge/8-red) | Prior patch | [v3.9.8](https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v3.9.8) |
| ![3](https://img.shields.io/badge/3-brightred)![9](https://img.shields.io/badge/9-darkred)![7](https://img.shields.io/badge/7-red)![1](https://img.shields.io/badge/1-orange) | Prior tweak | [v3.9.7.1](https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v3.9.7.1) |

<details>
<summary>Badge Color Convention</summary>

Each segment: `brightgreen → darkgreen → green → yellow` (current) / `brightred → darkred → red → orange` (prior)

</details>

## Important: OGG/OPUS Format Only

**This project uses OGG/OPUS as the exclusive audio format.** While the included AAXtoMP3 converter supports other formats (MP3, M4A, M4B, FLAC), the library browser, web UI, Docker container, and all tooling are designed and tested **only with OGG/OPUS files**.

OPUS offers superior audio quality at lower bitrates compared to MP3, making it ideal for audiobooks. I chose this format for my personal library and have no plans to support other formats.

<details>
<summary>What would need to change for other formats?</summary>

- Scanner: Update file extension detection (`.opus` → `.mp3`, etc.)
- Database schema: Potentially add format-specific metadata fields
- Web UI: Update MIME types in audio player, file extension filters
- Cover art handling: Different embedding methods per format
- Docker entrypoint: Update file discovery patterns
- API: Modify file serving and content-type headers

Pull requests welcome if you need this functionality.
</details>

## Components

### 1. Converter (`converter/`)

This project includes a **personal fork of [AAXtoMP3](https://github.com/KrumpetPirate/AAXtoMP3)** (v2.2) for converting Audible AAX/AAXC files to OGG/OPUS format. The original project by KrumpetPirate has been archived, and this fork includes essential fixes for modern AAXC file handling.

> **Note**: While AAXtoMP3 supports multiple output formats (MP3, M4A, M4B, FLAC, OPUS), this toolkit is configured exclusively for OPUS output. See the converter's [FORK_README.md](converter/FORK_README.md) for full documentation.

<details>
<summary>Fork modifications from original AAXtoMP3</summary>

**Bug Fixes:**

- Fixed `tmp_chapter_file: unbound variable` crash when chapter files are missing
- Fixed cover extraction for AAXC files (was using hardcoded `-activation_bytes` instead of `${decrypt_param}`)
- Made audible-cli chapter/cover files optional instead of required

**New Features:**

- **Opus cover art embedding** via Python mutagen library (FFmpeg cannot embed covers in OGG/Opus)
- Enhanced fallback handling - extracts metadata directly from AAXC when audible-cli files are missing
- Improved logging and user feedback during conversion

**Dependencies Added:**

- `mutagen` (optional) - Required for Opus cover art embedding

See [converter/CHANGELOG.md](converter/CHANGELOG.md) for version history.
</details>

### 2. Library (`library/`)

Web-based audiobook library browser with:

- Vintage library-themed interface
- Built-in audio player with automatic position saving (every 5 seconds, plus on scrub/skip)
- Play always resumes from last position
- Full-text search across titles, authors, and narrators
- **Author/Narrator autocomplete** with letter group filters (A-E, F-J, K-O, P-T, U-Z)
- **Collections sidebar** for browsing by category (Fiction, Nonfiction, Mystery, Sci-Fi, etc.)
- **Comprehensive sorting**: title, author/narrator first/last name, duration, publish date, acquired date, series with sequence, edition
- **Grouped view**: Author (Grouped A-Z) and Narrator (Grouped A-Z) with collapsible headers (v7.0+)
- **Normalized author/narrator data**: Multi-author books properly split into individual entities with admin correction tools (v7.0+)
- **Smart duplicate detection** by title/author/narrator or SHA-256 hash
- Cover art display with automatic extraction
- PDF supplement support (course materials, maps, etc.)
- **Genre sync** from Audible library export with 250+ genre categories
- **Narrator metadata sync** from Audible library export
- Production-ready HTTPS server with reverse proxy
- **Multi-user authentication** with TOTP, Passkey, and FIDO2 support (v5.0+)
- **Admin approval flow** for new user registration with secure claim tokens
- **Per-user playback positions** with encrypted auth database (SQLCipher)
- **My Library tab** with progress bars, listening history, download tracking, and hide/unhide books (v6.3+)
- **New books marquee** highlighting recently added audiobooks (v6.3+)
- **Admin activity audit** with filterable log and usage statistics (v6.3+)
- **Genre management** with bulk add/remove in Back Office (v6.3+)
- **Maintenance scheduling** with cron-based task automation, real-time WebSocket announcements, and admin dashboard
- **Web-based user management** — admins create, edit, and delete users with TOTP, Magic Link, or Passkey auth directly from the Back Office USERS tab (v7.4.1+)
- **Self-service My Account** — authenticated users change their username, email, auth method, or credentials from the shell header without admin involvement (v7.4.1+)
- **Audit logging** for all user management actions with paginated, filterable log in the Back Office (v7.4.1+)
- **Admin notifications** — in-app badge and email alerts to all admins for critical account changes (v7.4.1+)
- **Series metadata on library cards** — series name and book order number displayed on card overlays for series audiobooks (v8.0+)
- **Dynamic collections** — auto-generated browsable groupings from enrichment data (genres, narrators, decades, ratings) via `/api/collections` (v8.0+)
- **Per-user preferences** — key-value preference system persisting sort order, view mode, playback speed, and accessibility settings per user (v8.0+)
- **Accessibility quick panel** — slide-out panel with font size, contrast, reduced motion, and dyslexia-friendly font controls (v8.0+)
- **Account preferences UI** — user-facing settings page for display, notification, and accessibility preferences (v8.0+)
- **Multi-session login** — admin-configurable concurrent device sessions with global default and per-user override (v8.0.1.2+)

## Quick Start

### Browse Library

```bash
# Launch via systemd (recommended — production mode)
sudo systemctl start audiobook.target

# Opens https://localhost:8443 in your browser
# HTTP requests to port 8080 are automatically redirected to HTTPS
# Uses Gunicorn with geventwebsocket for production-ready performance and WebSocket support

# Or use legacy launcher (development mode, no systemd)
cd library
./launch-v2.sh  # Opens http://localhost:8090
```

**Note**: Your browser will show a security warning (self-signed certificate). Click "Advanced" → "Proceed to localhost" to continue.

### Convert Audiobooks

```bash
# Convert to OPUS (recommended, default for this project)
./converter/AAXtoMP3 --opus --single --use-audible-cli-data input.aaxc

# Interactive mode
./converter/interactiveAAXtoMP3
```

### Scan New Audiobooks

```bash
cd library/scanner
python3 scan_audiobooks.py

cd ../backend
python3 import_to_db.py
```

### Manage Duplicates

```bash
cd library

# Generate file hashes (sequential)
python3 scripts/generate_hashes.py

# Generate hashes in parallel (uses all CPU cores)
python3 scripts/generate_hashes.py --parallel

# Generate with specific worker count
python3 scripts/generate_hashes.py --parallel 8

# View hash statistics
python3 scripts/generate_hashes.py --stats

# Verify random sample of hashes
python3 scripts/generate_hashes.py --verify 20

# Find duplicates
python3 scripts/find_duplicates.py

# Remove duplicates (dry run)
python3 scripts/find_duplicates.py --remove

# Remove duplicates (execute)
python3 scripts/find_duplicates.py --execute
```

### Manage Supplements

Some Audible audiobooks include supplemental PDFs (course materials, maps, reference guides).

```bash
# Scan supplements directory and link to audiobooks
cd library/scripts
python3 scan_supplements.py --supplements-dir /path/to/supplements

# In Docker, supplements are scanned automatically on startup
```

Books with supplements show a red "PDF" badge in the UI. Click to download.

### Update Narrator Metadata

Narrator information is often missing from converted audio files. Sync from your Audible library:

```bash
# Export your Audible library metadata (requires audible-cli authentication)
audible library export -f json -o /path/to/Audiobooks/library_metadata.json

# Update database with narrator information (dry run first)
cd library/scripts
python3 update_narrators_from_audible.py

# Apply changes
python3 update_narrators_from_audible.py --execute
```

### Populate Genres

Genre information enables the Collections sidebar for browsing by category. Sync genres from your Audible library export:

```bash
# Export your Audible library metadata (if not already done)
audible library export -f json -o /path/to/Audiobooks/library_metadata.json

# Preview genre matches (dry run)
cd library/scripts
python3 populate_genres.py

# Apply changes
python3 populate_genres.py --execute
```

The script matches books by ASIN, exact title, or fuzzy title matching (85% threshold). This populates the genres table and enables collection-based filtering in the web UI.

### Multi-Source Audiobooks (Experimental - Disabled by Default)

> **⚠️ EXPERIMENTAL / NOT FULLY TESTED - USE AT YOUR OWN RISK**
>
> Multi-source audiobook support (Google Play, Chirp, Librivox, etc.) is **disabled by default**. The only fully tested and verified format is **Audible's AAXC**.
>
> **Known Issues with non-AAXC formats:**
>
> - Metadata extraction may be incomplete or incorrect
> - Chapter detection/ordering may fail for some sources
> - Cover art extraction is unreliable for many formats
> - Multi-reader audiobooks (e.g., Librivox) may not be handled correctly
>
> The `audiobooks-multiformat` service and related scripts are disabled. To enable at your own risk, uncomment the watch directories in `watch-multiformat-sources.sh`.
>
> PRs welcome if you want to improve multi-source support.
> See: [Roadmap Discussion](https://github.com/TheBoscoClub/Audiobook-Manager/discussions/2)

<details>
<summary>Multi-source scripts (click to expand)</summary>

Import audiobooks from sources beyond Audible (Google Play, Librivox, Chirp, etc.):

```bash
# Process Google Play audiobook (ZIP or M4A files)
cd library/scripts
python3 google_play_processor.py /path/to/audiobook.zip --import-db --execute

# Process directory of MP3/M4A chapter files
python3 google_play_processor.py /path/to/chapters/ --import-db --execute

# Enrich metadata from OpenLibrary API
python3 populate_from_openlibrary.py --execute

# Download free audiobooks from Librivox
python3 librivox_downloader.py --search "pride and prejudice"
python3 librivox_downloader.py --id 12345  # Download by Librivox ID
```

The Google Play processor:

- Accepts ZIP files, directories of chapters, or single audio files (MP3/M4A/M4B)
- Merges chapters into a single OPUS file at 64kbps (optimal for speech)
- Extracts and embeds cover art
- Enriches metadata from OpenLibrary (title, author, subjects)
- Calculates SHA-256 hash automatically
- Imports directly to database with `--import-db`

</details>

### Populate Sort Fields

Extract author/narrator names and series info for enhanced sorting:

```bash
cd library/scripts

# Preview changes
python3 populate_sort_fields.py

# Apply changes
python3 populate_sort_fields.py --execute
```

This extracts:

- Author first/last name from full name (handles "J.R.R. Tolkien", "John le Carré", etc.)
- Narrator first/last name
- Series sequence numbers from titles ("Book 1", "#2", "Part 3", Roman numerals)
- Edition information ("20th Anniversary Edition", "Unabridged", etc.)
- Acquired date from file modification time

## Installation

### Quick Install (From GitHub Releases)

Install the latest release without cloning the repository:

```bash
# One-line installer
curl -sSL https://github.com/TheBoscoClub/Audiobook-Manager/raw/main/bootstrap-install.sh | bash

# Or download and install manually
wget https://github.com/TheBoscoClub/Audiobook-Manager/releases/latest/download/audiobooks-*.tar.gz
tar -xzf audiobooks-*.tar.gz
cd audiobooks-*
./install.sh
```

### From Source

Clone the repository and run the interactive installer:

```bash
git clone https://github.com/TheBoscoClub/Audiobook-Manager.git
cd Audiobook-Manager
./install.sh
```

You'll be presented with a menu to choose:

- **System Installation** - Installs application to `/opt/audiobooks`, commands to `/usr/local/bin`, config to `/etc/audiobooks` (requires sudo). Services are automatically enabled and started.
- **User Installation** - Installs to `~/.local/bin` and `~/.config/audiobooks` (no root required)
- **Exit** - Exit without changes

### Command-Line Options

```bash
./install.sh --system              # Skip menu, system install
./install.sh --user                # Skip menu, user install
./install.sh --data-dir /path      # Specify data directory
./install.sh --uninstall           # Remove installation
./install.sh --no-services         # Skip systemd services
```

### Port Conflict Detection

The installer automatically checks if the required ports (5001, 8443, 8080) are available before installation. If a port is in use, you'll see options to:

1. Choose an alternate port
2. Continue anyway (if you plan to stop the conflicting service)
3. Abort installation

### Storage Tier Detection

The installer automatically detects storage types (NVMe, SSD, HDD) and warns if performance-critical components would be placed on slow storage:

| Component | Recommended | Why |
|-----------|-------------|-----|
| Database (audiobooks.db) | NVMe/SSD | High random I/O; 100x faster queries |
| Index files (.index/) | NVMe/SSD | Frequently accessed during operations |
| Audio Library (Library/) | HDD OK | Sequential streaming works well on HDD |

If the database path is on HDD, you'll see a warning with the option to cancel and adjust paths. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#storage-architecture) for detailed recommendations.

### tmpfs Considerations

If your `/tmp` directory is mounted as tmpfs (RAM-based filesystem), you'll need to ensure required directories are recreated on each boot. This is a common configuration to reduce SSD/NVMe wear.

**Why tmpfs is recommended for /tmp:**

- Reduces write wear on SSDs/NVMes (especially important for high-write workloads)
- Faster I/O since it's RAM-backed
- Auto-cleans on reboot

**Required /tmp directories:**

| Directory | Purpose | Created By |
|-----------|---------|------------|
| `/tmp/audiobook-staging` | In-progress conversions and downloads | tmpfiles.d |
| `/tmp/audiobook-triggers` | Inter-service signaling | tmpfiles.d |

**Setup:** The installer configures `/etc/tmpfiles.d/audiobooks.conf` to recreate these directories on boot. If you're experiencing issues with services failing after reboot, verify:

```bash
# Check tmpfiles.d config exists
cat /etc/tmpfiles.d/audiobooks.conf

# Manually recreate directories if needed
sudo systemd-tmpfiles --create /etc/tmpfiles.d/audiobooks.conf

# Verify directories exist
ls -la /tmp/audiobook-staging /tmp/audiobook-triggers
```

**Symptoms of missing directories:**

- Services fail with "No such file or directory" errors
- Converter reports files stuck in queue but shows "idle"
- Mover service fails silently

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#tmpfs-runtime-directories) for detailed tmpfs architecture.

Both installation modes:

- Create `audiobooks` service account (system install) or use current user (user install)
- Create configuration files with auth and remote access templates
- Generate auth encryption key (64 hex chars, mode 0600)
- Initialize database from `schema.sql`
- Set up Python virtual environment (validated with `python --version`)
- Install Python dependencies from `requirements.txt`
- Generate self-signed SSL certificates
- Install systemd services with proper `User`/`Group`/`WorkingDirectory`

After installation, use these commands:

```bash
audiobook-api      # Start API server
audiobook-web      # Start web server (HTTPS)
audiobook-scan     # Scan audiobook library
audiobook-import   # Import to database
audiobook-config   # Show configuration
```

## Upgrading

### Docker

Docker installations upgrade by pulling a new image:

```bash
# Pull latest image and recreate container
docker-compose pull
docker-compose up -d

# Or with docker directly
docker pull theboscoclub/audiobook-manager:latest
docker stop audiobooks && docker rm audiobooks
docker run -d --name audiobooks ... theboscoclub/audiobook-manager:latest

# Check running version
docker exec audiobooks cat /app/VERSION
```

Your data persists in mounted volumes (`/audiobooks`, `/app/data`).

### Standalone Installation (From GitHub)

Upgrade your installation directly from GitHub releases:

```bash
# Upgrade to latest version
audiobook-upgrade

# Upgrade to specific version
audiobook-upgrade --version 7.4.0

# Check for updates without installing
audiobook-upgrade --check
```

### From Local Project

If you have the repository cloned locally:

```bash
# From within the project directory
./upgrade.sh --target /opt/audiobooks

# Or specify both source and target
./upgrade.sh --from-project /path/to/repo --target /opt/audiobooks
```

### API Architecture Migration

Switch between monolithic and modular Flask architectures:

```bash
# Check current architecture
./migrate-api.sh --status

# Switch to modular (Flask Blueprints)
./migrate-api.sh --to-modular --target /opt/audiobooks

# Switch to monolithic (single file)
./migrate-api.sh --to-monolithic --target /opt/audiobooks

# Dry run (show what would be done)
./migrate-api.sh --to-modular --dry-run
```

**Note:** Migration automatically stops services before switching and restarts them after.

## Configuration

Configuration is loaded from multiple sources in priority order:

1. System config: `/etc/audiobooks/audiobooks.conf`
2. User config: `~/.config/audiobooks/audiobooks.conf`
3. Environment variables

### Configuration Variables

| Variable | Description |
|----------|-------------|
| `AUDIOBOOKS_DATA` | Root data directory |
| `AUDIOBOOKS_LIBRARY` | Converted audiobook files |
| `AUDIOBOOKS_SOURCES` | Source AAXC files |
| `AUDIOBOOKS_SUPPLEMENTS` | PDF supplements |
| `AUDIOBOOKS_HOME` | Application installation directory |
| `AUDIOBOOKS_DATABASE` | SQLite database path |
| `AUDIOBOOKS_COVERS` | Cover art cache |
| `AUDIOBOOKS_CERTS` | SSL certificate directory |
| `AUDIOBOOKS_LOGS` | Log files directory |
| `AUDIOBOOKS_STAGING` | Temporary staging directory for conversions (default: /tmp/audiobook-staging) |
| `AUDIOBOOKS_VENV` | Python virtual environment path |
| `AUDIOBOOKS_CONVERTER` | Path to AAXtoMP3 converter script |
| `AUDIOBOOKS_API_PORT` | API server port (default: 5001) |
| `AUDIOBOOKS_WEB_PORT` | HTTPS web server port (default: 8443) |
| `AUDIOBOOKS_BIND_ADDRESS` | Server bind address (default: 0.0.0.0) |
| `AUDIOBOOKS_HTTP_REDIRECT_PORT` | HTTP→HTTPS redirect port (default: 8080) |
| `AUDIOBOOKS_HTTP_REDIRECT_ENABLED` | Enable HTTP redirect server (default: true) |
| `AUDIOBOOKS_HTTPS_ENABLED` | Enable HTTPS for web server (default: true) |
| ~~`AUDIOBOOKS_USE_WAITRESS`~~ | Removed in v7.2 — migrated to Gunicorn+geventwebsocket |
| `AUTH_ENABLED` | Enable authentication for remote access (default: false) |
| `AUTH_DATABASE` | Auth database path (default: /var/lib/audiobooks/auth.db) |
| `AUTH_KEY_FILE` | Auth encryption key path (default: /etc/audiobooks/auth.key) |
| `AUDIOBOOKS_HOSTNAME` | Public domain for WebAuthn/email links (auto-detected if unset) |
| `BASE_URL` | Base URL for email links (auto-detected if unset) |
| `CORS_ORIGIN` | CORS allowed origin (default: * for standalone, set for remote) |

### Override via Environment

```bash
AUDIOBOOKS_LIBRARY=/mnt/nas/audiobooks ./launch.sh
```

### View Current Configuration

```bash
audiobook-config
```

## Directory Structure

```text
Audiobooks/
├── etc/
│   └── audiobooks.conf.example  # Config template
├── lib/
│   └── audiobook-config.sh     # Config loader (shell)
├── install.sh                   # Unified installer (interactive)
├── install-user.sh              # User installation (standalone)
├── install-system.sh            # System installation (standalone)
├── install-services.sh          # Legacy service installer
├── uninstall.sh                 # Comprehensive uninstaller (dynamic discovery)
├── upgrade.sh                   # Upgrade from GitHub/local/remote VM (--remote, --yes)
├── migrate-api.sh               # Switch API architecture
├── launch.sh                    # Quick launcher
├── converter/                   # AAXtoMP3 conversion tools
│   ├── AAXtoMP3                 # Main conversion script
│   └── interactiveAAXtoMP3
├── library/                     # Web library interface
│   ├── config.py                # Python configuration module
│   ├── auth/                    # Authentication module (v5.0+)
│   │   ├── database.py          # SQLCipher encryption wrapper
│   │   ├── models.py            # User, Session, AccessRequest repositories
│   │   ├── passkey.py           # WebAuthn/FIDO2 registration & auth
│   │   ├── totp.py              # TOTP (authenticator app) support
│   │   ├── backup_codes.py      # Single-use recovery codes
│   │   ├── audit.py             # Audit log model and repository (v7.4.1+)
│   │   ├── cli.py               # Admin CLI tool (audiobook-user)
│   │   ├── inbox_cli.py         # Admin inbox management CLI
│   │   ├── notify_cli.py        # Notification management CLI
│   │   └── schema.sql           # Auth database schema (19 tables, v9)
│   ├── backend/
│   │   ├── api_server.py        # Flask server launcher
│   │   ├── api_modular/         # Modular Flask Blueprints
│   │   │   ├── __init__.py
│   │   │   ├── auth.py          # Auth endpoints + admin_or_localhost decorator (v5.0+)
│   │   │   ├── core.py          # App factory, CORS, error handlers
│   │   │   ├── audiobooks.py    # Audiobook listing, streaming, details
│   │   │   ├── collections.py   # Genre-based collections
│   │   │   ├── duplicates.py    # Duplicate detection
│   │   │   ├── editions.py      # Edition grouping
│   │   │   ├── supplements.py   # PDF companion files
│   │   │   ├── position_sync.py # Playback position sync
│   │   │   ├── utilities.py     # CRUD, imports, exports
│   │   │   ├── utilities_system.py  # Admin: services, upgrades (guarded)
│   │   │   ├── utilities_crud.py    # Database CRUD + genre management
│   │   │   ├── utilities_db.py      # Database maintenance
│   │   │   ├── utilities_conversion.py # Conversion operations
│   │   │   ├── user_state.py        # Per-user history, downloads, library (v6.3+)
│   │   │   ├── admin_activity.py    # Activity audit log and stats (v6.3+)
│   │   │   ├── grouped.py          # Grouped A-Z view by author/narrator (v7.0+)
│   │   │   └── admin_authors.py    # Admin author/narrator management (v7.0+)
│   │   ├── import_to_db.py      # Database importer
│   │   ├── name_parser.py       # Multi-name parsing and sort key generation (v7.0+)
│   │   ├── migrate_to_normalized_authors.py  # Data migration for normalized tables (v7.0+)
│   │   ├── migrations/          # Schema migration SQL files (v7.0+)
│   │   │   └── 011_multi_author_narrator.sql
│   │   ├── schema.sql           # Database schema
│   │   └── operation_status.py  # Operation tracking
│   ├── scanner/
│   │   └── scan_audiobooks.py   # Metadata extraction from audio files
│   ├── scripts/
│   │   ├── generate_hashes.py           # SHA-256 hash generation (parallel)
│   │   ├── find_duplicates.py           # Duplicate detection & removal
│   │   ├── scan_supplements.py          # PDF supplement scanner
│   │   ├── populate_sort_fields.py      # Extract name/series/edition info
│   │   ├── populate_genres.py           # Sync genres from Audible export
│   │   ├── populate_from_openlibrary.py # Enrich from OpenLibrary API
│   │   ├── update_narrators_from_audible.py  # Sync narrator metadata
│   │   ├── google_play_processor.py     # Process multi-source audiobooks
│   │   ├── librivox_downloader.py       # Download free Librivox audiobooks
│   │   ├── cleanup_audiobook_duplicates.py   # Database cleanup
│   │   ├── fix_audiobook_authors.py     # Author metadata repair
│   │   ├── enrich_from_audible.py       # Enrich metadata from Audible API
│   │   ├── enrich_from_isbn.py          # Enrich from Google Books / Open Library
│   │   ├── enrich_single.py             # Inline enrichment for single book
│   │   ├── populate_series_from_audible.py  # Bulk series data from Audible
│   │   ├── verify_metadata.py           # Cross-reference & auto-correct metadata
│   │   └── utils/
│   │       └── openlibrary_client.py    # OpenLibrary API client
│   └── web-v2/
│       ├── shell.html           # Outer frame with persistent player bar
│       ├── index.html           # Main library (loads inside shell iframe)
│       ├── about.html           # Credits, attributions, version info
│       ├── help.html            # User guide and FAQ
│       ├── admin.html           # Admin panel (user management)
│       ├── utilities.html       # Back office (scan, import, maintenance)
│       ├── login.html           # TOTP / Passkey / FIDO2 login
│       ├── register.html        # Access request form
│       ├── claim.html           # Credential setup with claim token
│       ├── verify.html          # Email / token verification
│       ├── contact.html         # Contact / feedback form
│       ├── 401.html             # Unauthorized error page
│       ├── 403.html             # Forbidden error page
│       ├── js/
│       │   ├── library.js       # Library frontend (search, sort, player)
│       │   ├── shell.js         # Shell frame (viewport fix, player controls)
│       │   ├── account.js       # Self-service My Account modal (v7.4.1+)
│       │   ├── utilities.js     # Back Office utilities tab logic
│       │   ├── websocket.js     # WebSocket client for live connections/audit events
│       │   ├── webauthn.js      # WebAuthn registration and authentication helpers
│       │   ├── marquee.js       # New books marquee logic (v6.3+)
│       │   ├── tutorial.js      # Help/tutorial overlay
│       │   ├── session-persistence.js # Playback position persistence
│       │   ├── maint-sched.js   # Maintenance scheduler UI (v7.0+)
│       │   └── maintenance-banner.js  # Maintenance banner display
│       ├── css/
│       │   ├── library.css      # Main library styling
│       │   ├── shell.css        # Shell frame and player bar layout
│       │   ├── account.css      # My Account modal (extracted from shell.css, v7.4.2)
│       │   ├── theme-art-deco.css # Art Deco visual theme
│       │   ├── responsive.css   # Mobile/tablet breakpoints
│       │   ├── modals.css       # Modal dialogs
│       │   ├── about.css        # About page styling
│       │   └── help.css         # Help page styling
│       ├── proxy_server.py      # HTTPS reverse proxy (serves / as shell.html)
│       └── redirect_server.py   # HTTP→HTTPS redirect
├── Dockerfile                   # Docker build file
├── docker-compose.yml           # Docker Compose config
└── README.md
```

### Installed Directory Structure (System Installation)

After system installation, files are organized as follows:

```text
/opt/audiobooks/                    # Application installation (AUDIOBOOKS_HOME)
├── scripts/                        # Canonical script location
│   ├── audiobook-convert
│   ├── download-new-audiobooks
│   ├── move-staged-audiobooks
│   ├── cleanup-stale-indexes       # Remove deleted files from indexes
│   ├── build-conversion-queue      # Build/rebuild conversion queue
│   ├── upgrade.sh
│   └── ...
├── library/                        # Python application
│   ├── backend/                    # Flask API
│   ├── scanner/                    # Metadata extraction
│   ├── web-v2/                     # Web interface
│   └── venv/                       # Python virtual environment
├── converter/                      # AAXtoMP3
└── VERSION

/usr/local/bin/                     # Symlinks for PATH accessibility
├── audiobook-api                  # Wrapper script
├── audiobook-convert -> /opt/audiobooks/scripts/audiobook-convert
├── audiobook-download -> /opt/audiobooks/scripts/download-new-audiobooks
├── audiobook-move-staged -> /opt/audiobooks/scripts/move-staged-audiobooks
└── ...

${AUDIOBOOKS_DATA}/                 # User data directory (e.g., /srv/audiobooks)
├── Library/                        # Converted audiobooks (AUDIOBOOKS_LIBRARY)
├── Sources/                        # Original AAXC files (AUDIOBOOKS_SOURCES)
├── Supplements/                    # PDF supplements
├── .covers/                        # Cover art cache (AUDIOBOOKS_COVERS)
├── .index/                         # Index files for tracking
│   ├── source_checksums.idx        # MD5 checksums of source files
│   ├── library_checksums.idx       # MD5 checksums of library files
│   ├── source_asins.idx            # ASIN tracking for sources
│   ├── converted.idx               # Converted title tracking
│   ├── converted_asins.idx         # Converted ASIN tracking
│   └── queue.txt                   # Conversion queue
└── logs/                           # Application logs

/var/lib/audiobooks/                # Database (on fast storage)
└── db/
    └── audiobooks.db               # SQLite database (AUDIOBOOKS_DATABASE)

/etc/audiobooks/                    # System configuration
├── audiobooks.conf                 # Main config file
└── certs/                          # SSL certificates

/tmp/                               # Runtime directories (tmpfs recommended)
├── audiobook-staging/              # In-progress conversions (cleared on reboot)
│   └── [author]/[title]/           # Working directories per audiobook
└── audiobook-triggers/             # Inter-service signaling
    └── conversion-complete         # Signals mover when batch done
```

> **Note:** If `/tmp` is a tmpfs (RAM-based), these directories are recreated on boot via `/etc/tmpfiles.d/audiobooks.conf`. See [tmpfs Considerations](#tmpfs-considerations) for setup details.

**Architecture Notes:**

- Scripts are installed to `/opt/audiobooks/scripts/` (canonical location)
- Symlinks in `/usr/local/bin/` point to canonical scripts, so upgrades automatically update commands
- Wrapper scripts source from `/opt/audiobooks/lib/audiobook-config.sh` (canonical path)
- Backward-compat symlink: `/usr/local/lib/audiobooks` → `/opt/audiobooks/lib/`
- User data (`${AUDIOBOOKS_DATA}`) is separate from application code (`/opt/audiobooks/`)
- Database is placed in `/var/lib/` for fast storage (NVMe/SSD recommended)
- Services are automatically enabled and started after installation

## Web Interface Features

### Collections Sidebar

Browse your library by curated categories:

- **Toggle button**: Click "Collections" in the results bar to open the sidebar
- **Categories**: Special (The Great Courses), Main Genres (Fiction, Nonfiction), Nonfiction (History, Science, Biography, Memoir), Subgenres (Mystery & Thriller, Science Fiction, Fantasy, Romance)
- **Active filter badge**: Shows current collection on toggle button
- **Close options**: × button, click overlay, or press Escape

### Search & Filtering

- **Full-text search**: Search across titles, authors, and narrators
- **Author filter**: Autocomplete dropdown with A-E, F-J, K-O, P-T, U-Z letter groups
- **Narrator filter**: Autocomplete dropdown with book counts and letter groups
- **Collection filter**: Browse by category via Collections sidebar
- **Clear button**: Reset all filters with one click

### Sorting Options

| Sort By | Description |
|---------|-------------|
| Title (A-Z/Z-A) | Alphabetical by title |
| Author Last Name | Sort by author's last name (Smith, King, etc.) |
| Author First Name | Sort by author's first name |
| Author Full Name | Sort by full author name as displayed |
| Narrator Last Name | Sort by narrator's last name |
| Narrator First Name | Sort by narrator's first name |
| Duration | Longest or shortest first |
| Recently Acquired | By file modification date |
| Newest/Oldest Published | By publication year |
| Author (Grouped A-Z) | Books grouped under collapsible author headers, sorted by last name (v7.0+) |
| Narrator (Grouped A-Z) | Books grouped under collapsible narrator headers, sorted by last name (v7.0+) |
| Series (A-Z with sequence) | Groups series together, ordered by book number |
| Edition | Sort by edition type |

### Duplicate Detection

Four detection methods available in the Back Office Duplicates tab:

1. **By Title/Author/Narrator**: Finds books with matching metadata (may be different files)
2. **By SHA-256 Hash**: Finds byte-identical Library files using cryptographic hashes (from database)
3. **Source File Checksums**: Fast MD5 partial checksums to find duplicate .aaxc files in Sources folder
4. **Library File Checksums**: Fast MD5 partial checksums to find duplicate .opus files in Library folder

### My Library Tab

Personalized view of your audiobook activity (requires authentication):

- **Progress bars**: Visual completion percentage for each book you've listened to
- **Recently Listened**: Quick access to books you've been listening to, sorted by last played
- **Listening History**: Complete log of your listening sessions with timestamps and durations
- **Download History**: Track which books you've downloaded
- **Hide/Unhide**: Remove finished or unwanted books from view while preserving all data; restore from the Hidden view

### New Books Marquee

An Art Deco neon-styled marquee highlights audiobooks added since your last visit. Click "Dismiss" to mark them as seen. The marquee only appears when new books exist.

### About Page

Version info (displayed prominently at the top, fetched live from the API), credits, third-party attributions (FFmpeg, SQLCipher, Flask, mutagen, PyOTP, FIDO2/WebAuthn, Howler.js), and project links. Accessible from the Help page header.

### Shell Architecture

The web UI uses a shell + iframe design. `shell.html` is the persistent outer frame containing the audio player bar, while `index.html` loads inside an iframe. The proxy serves shell content at the clean URL `/` — navigating to `/shell.html` returns a 301 redirect to `/`. The `visualViewport` API dynamically adjusts layout height to prevent mobile browser chrome from obscuring the player controls.

### Audio Player

- Play/pause with progress bar
- Skip forward/back 30 seconds
- Adjustable playback speed (0.5x - 2.5x)
- Volume control
- **Position saving**: Automatically saves playback position per user per book
- **Resume playback**: Click Play on any book to resume from last position

## Playback Position Tracking

Per-user playback positions are tracked locally in the encrypted auth database (SQLCipher). Each authenticated user gets independent position tracking.

### How It Works

- **Automatic saving**: Web player saves position every 5 seconds to both localStorage and the API, and immediately on scrub, +30s, or -30s
- **Per-user isolation**: Each user has their own position for every book (stored in the auth database)
- **Resume anywhere**: Log in from any browser and resume where you left off
- **Listening history**: All sessions are logged with start/end positions and duration

### Position API

```bash
# Get position for a book
curl -s http://localhost:5001/api/position/<audiobook_id>

# Update position
curl -X PUT http://localhost:5001/api/position/<audiobook_id> \
  -H "Content-Type: application/json" \
  -d '{"position_ms": 45000}'
```

For detailed documentation, see [docs/POSITION_SYNC.md](docs/POSITION_SYNC.md).

> **Note**: Audible cloud sync was removed in favor of the self-contained per-user system. Positions are now fully local — no external service dependencies.

## Authentication (v5.0+)

Audiobook-Manager supports multi-user authentication with three methods:

| Method | How It Works | Best For |
|--------|-------------|----------|
| **TOTP** | Time-based codes via authenticator app ([2FAS](https://2fas.com), Google Authenticator, Aegis) | Most users |
| **Passkey** | Biometrics, phone, or password manager (Bitwarden, 1Password) | Convenience |
| **FIDO2** | Hardware security key (YubiKey, Titan) | Maximum security |

### How Authentication Works

- Authentication is controlled by `AUTH_ENABLED` in your config (default: `false`)
- **Standalone mode** (`AUTH_ENABLED=false`): Library endpoints are open, admin endpoints restricted to localhost only
- **Remote mode** (`AUTH_ENABLED=true`): All API endpoints and web UI require a valid session, admin endpoints require admin role
- Sessions use secure HTTP-only cookies with SameSite=Lax protection
- The auth database is encrypted at rest using SQLCipher (AES-256)
- Admin endpoints are **never** wide-open regardless of mode (dual-mode security, v6.0+)

### First User Setup (Bootstrap)

The first user to register is **automatically approved as admin** — no approval needed:

```bash
# 1. Navigate to the web UI
open https://localhost:8443

# 2. You'll be redirected to the login page
# 3. Click "Request Access" and choose a username
# 4. As the first user, you'll receive your TOTP secret immediately
# 5. Scan the QR code with your authenticator app
# 6. Save your 8 backup codes in a safe place
# 7. Log in with your TOTP code
```

### Adding More Users

After the first user, new registrations require admin approval:

1. **New user** visits the site and clicks "Request Access"
2. **New user** receives a **claim token** (16-character code) — they must save this
3. **Admin** reviews the request in the Admin panel and approves/denies
4. **New user** enters their claim token to set up credentials (TOTP, Passkey, or FIDO2)
5. **New user** receives 8 single-use backup codes for account recovery

Admins can also pre-approve users with **invitations** (`POST /auth/admin/users/invite`).

### Admin Capabilities

- Approve or deny access requests
- Invite new users with pre-approved accounts
- Toggle admin and download permissions per user
- Configure multi-session login globally and per-user (v8.0.1.2+)
- View and manage active sessions
- Send system notifications to users

### WebAuthn Configuration

WebAuthn (Passkey/FIDO2) auto-configures from your deployment settings:

| Setting | Source | Default |
|---------|--------|---------|
| RP ID | `AUDIOBOOKS_HOSTNAME` | `localhost` |
| Origin | Derived from hostname + port + HTTPS | `https://localhost:8443` |
| RP Name | `WEBAUTHN_RP_NAME` | `The Library` |

For custom deployments, override via environment or config:

```bash
WEBAUTHN_RP_ID=audiobooks.example.com
WEBAUTHN_ORIGIN=https://audiobooks.example.com
```

### Standalone Mode (Default)

For single-user or LAN-only deployments (default configuration):

```bash
# In /etc/audiobooks/audiobooks.conf (this is the default)
AUTH_ENABLED=false
```

When disabled, library endpoints are accessible without login. Admin endpoints (service control, upgrades) are restricted to localhost only — they cannot be accessed from remote IPs. This is the recommended mode for home servers not exposed to the internet.

### Remote Access Mode

For internet-facing deployments behind a reverse proxy:

```bash
# In /etc/audiobooks/audiobooks.conf
AUTH_ENABLED=true
AUDIOBOOKS_HOSTNAME=library.example.com
BASE_URL=https://library.example.com
CORS_ORIGIN=https://library.example.com
```

When enabled, all endpoints require authentication. Admin operations require an authenticated admin user. See [Secure Remote Access Spec](docs/SECURE_REMOTE_ACCESS_SPEC.md) for full deployment guide.

### Related Documentation

- [Secure Remote Access Spec](docs/SECURE_REMOTE_ACCESS_SPEC.md) — Full design specification
- [Auth Runbook](docs/AUTH_RUNBOOK.md) — Operational procedures and admin guide
- [Auth Failure Modes](docs/AUTH_FAILURE_MODES.md) — Troubleshooting authentication issues

## REST API

The library exposes a REST API on port 5001:

### Core Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/audiobooks` | GET | List audiobooks with pagination, search, filtering, sorting |
| `/api/audiobooks/<id>` | GET | Get single audiobook details |
| `/api/audiobooks/<id>` | PUT | Update audiobook metadata |
| `/api/audiobooks/<id>` | DELETE | Delete audiobook from library |
| `/api/collections` | GET | List available collections with book counts |
| `/api/stats` | GET | Library statistics (counts, total hours) |
| `/api/filters` | GET | Available filter options (authors, narrators, genres) |
| `/api/narrator-counts` | GET | Narrator names with book counts |
| `/api/stream/<id>` | GET | Stream audio file (supports range requests) |
| `/covers/<filename>` | GET | Get cover art image |
| `/health` | GET | API health check |

### Duplicate Management

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/duplicates` | GET | List all duplicates |
| `/api/duplicates/by-title` | GET | Find duplicates by title/author/narrator |
| `/api/duplicates/delete` | POST | Delete duplicate files |
| `/api/duplicates/verify` | POST | Verify duplicate detection |
| `/api/hash-stats` | GET | Hash generation statistics |

### Supplements

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/supplements` | GET | List all supplements |
| `/api/supplements/stats` | GET | Supplement statistics |
| `/api/supplements/<id>/download` | GET | Download PDF supplement |
| `/api/supplements/scan` | POST | Scan for new supplements |

### Bulk Operations

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/audiobooks/bulk-update` | POST | Update multiple audiobooks |
| `/api/audiobooks/bulk-delete` | POST | Delete multiple audiobooks |
| `/api/audiobooks/missing-narrator` | GET | List books without narrator |
| `/api/audiobooks/missing-hash` | GET | List books without hash |

### Utilities (Back Office)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/utilities/add-new` | POST | Add new audiobooks (incremental scan) |
| `/api/utilities/rescan` | POST | Full library rescan |
| `/api/utilities/rescan-async` | POST | Async full library rescan |
| `/api/utilities/reimport` | POST | Reimport metadata to database |
| `/api/utilities/reimport-async` | POST | Async reimport metadata |
| `/api/utilities/generate-hashes` | POST | Generate SHA-256 hashes |
| `/api/utilities/generate-hashes-async` | POST | Async hash generation |
| `/api/utilities/generate-checksums-async` | POST | Async MD5 checksum generation (Sources + Library) |
| `/api/utilities/vacuum` | POST | Vacuum database |
| `/api/utilities/export-db` | GET | Export SQLite database |
| `/api/utilities/export-json` | GET | Export as JSON |
| `/api/utilities/export-csv` | GET | Export as CSV |

#### Library Maintenance (v3.6.0+)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/utilities/populate-sort-fields-async` | POST | Generate author_sort/title_sort |
| `/api/utilities/rebuild-queue-async` | POST | Rebuild conversion queue |
| `/api/utilities/cleanup-indexes-async` | POST | Remove stale index entries |

> **Note**: Maintenance endpoints accept `{"dry_run": true}` (default) for preview mode.
> Set `{"dry_run": false}` to apply changes.

### Operation Status (Long-running tasks)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/operations/status/<id>` | GET | Get operation status |
| `/api/operations/active` | GET | List active operations |
| `/api/operations/all` | GET | List all operations |
| `/api/operations/cancel/<id>` | POST | Cancel running operation |

### System Administration (v3.6.0+)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/system/health` | GET | Health check (unauthenticated, for monitoring) |
| `/api/system/version` | GET | Get installed version |
| `/api/system/services` | GET | Get status of all services |
| `/api/system/services/<name>/start` | POST | Start a service |
| `/api/system/services/<name>/stop` | POST | Stop a service |
| `/api/system/services/<name>/restart` | POST | Restart a service |
| `/api/system/services/start-all` | POST | Start all services |
| `/api/system/services/stop-all` | POST | Stop processing services |
| `/api/system/upgrade` | POST | Start upgrade (async) |
| `/api/system/upgrade/status` | GET | Get upgrade progress |
| `/api/system/projects` | GET | List available project dirs |

> **Note**: Service control and upgrades use a privilege-separated helper service
> pattern. The API writes requests to `/var/lib/audiobooks/.control/` which triggers
> a root-privileged helper via systemd path unit.

### Position Tracking (v3.7.2+)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/position/<id>` | GET | Get playback position for audiobook |
| `/api/position/<id>` | PUT | Update local playback position |
| `/api/position/history/<id>` | GET | Get position history for audiobook |

### Per-User State (v6.3+)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/user/history` | GET | User's listening history (paginated, filterable by date) |
| `/api/user/downloads` | GET | User's download history (paginated) |
| `/api/user/downloads/<id>/complete` | POST | Record download completion |
| `/api/user/library` | GET | Personalized library with progress bars and recently listened |
| `/api/user/new-books` | GET | Books added since user's last visit |
| `/api/user/new-books/dismiss` | POST | Mark new books as seen |

> **Note**: All `/api/user/*` endpoints require authentication.

### Genre Management (v6.3+)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/genres` | GET | List all genres with book counts |
| `/api/audiobooks/<id>/genres` | PUT | Set genres for a single audiobook (replace mode) |
| `/api/audiobooks/bulk-genres` | POST | Add or remove genres across multiple audiobooks |

### Grouped View (v7.0+)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/audiobooks/grouped` | GET | Books grouped by author or narrator (`?by=author\|narrator`) |

### Admin Author/Narrator Management (v7.0+)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/admin/authors/<id>` | PUT | Rename an author |
| `/api/admin/authors/merge` | POST | Merge duplicate authors |
| `/api/admin/books/<id>/authors` | PUT | Reassign authors for a book |
| `/api/admin/narrators/<id>` | PUT | Rename a narrator |
| `/api/admin/narrators/merge` | POST | Merge duplicate narrators |
| `/api/admin/books/<id>/narrators` | PUT | Reassign narrators for a book |

### Admin Activity Audit (v6.3+)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/admin/activity` | GET | Activity log with filters (user, type, date range, pagination) |
| `/api/admin/activity/stats` | GET | Aggregate stats (listens, downloads, active users, top content) |

> **Note**: All `/api/admin/*` endpoints require admin role.

### Authentication (v5.0+)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/auth/login` | POST | Authenticate with TOTP or WebAuthn |
| `/auth/logout` | POST | Invalidate current session |
| `/auth/check` | GET | Check if user is authenticated |
| `/auth/login/auth-type` | POST | Determine user's auth method |
| `/auth/login/webauthn/begin` | POST | Start WebAuthn authentication |
| `/auth/login/webauthn/complete` | POST | Complete WebAuthn authentication |
| `/auth/register/start` | POST | Submit access request |
| `/auth/register/claim` | POST | Claim credentials with TOTP |
| `/auth/register/claim/validate` | POST | Validate claim token |
| `/auth/register/claim/webauthn/begin` | POST | Start WebAuthn registration for claim |
| `/auth/register/claim/webauthn/complete` | POST | Complete WebAuthn claim registration |
| `/auth/me` | GET/PUT | Get or update current user info |
| `/auth/user/me/username` | PUT | Self-service: change own username (v7.4.1+) |
| `/auth/user/me/email` | PUT | Self-service: change own email (v7.4.1+) |
| `/auth/user/me/auth-method` | PUT | Self-service: switch auth method (v7.4.1+) |
| `/auth/user/me/reset-credentials` | POST | Self-service: reset own credentials (v7.4.1+) |
| `/auth/recover/backup-code` | POST | Use backup code for recovery |
| `/auth/recover/regenerate-codes` | POST | Generate new backup codes |
| `/auth/health` | GET | Auth system health check |

#### Admin Endpoints (requires admin role)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/auth/admin/users` | GET | List all users |
| `/auth/admin/users/create` | POST | Create user (TOTP/Magic Link/Passkey) — returns setup info |
| `/auth/admin/users/invite` | POST | Invite new user (pre-approved) |
| `/auth/admin/users/<id>` | PUT/DELETE | Update or delete user (legacy) |
| `/auth/admin/users/<id>/username` | PUT | Change username (v7.4.1+) |
| `/auth/admin/users/<id>/email` | PUT | Change email (v7.4.1+) |
| `/auth/admin/users/<id>/roles` | PUT | Update admin/download flags (v7.4.1+) |
| `/auth/admin/users/<id>/auth-method` | PUT | Switch auth method (v7.4.1+) |
| `/auth/admin/users/<id>/reset-credentials` | POST | Reset TOTP/magic link/passkey credentials (v7.4.1+) |
| `/auth/admin/users/<id>/setup-info` | GET | Re-fetch setup info for incomplete enrollment (v7.4.1+) |
| `/auth/admin/users/<id>/delete` | DELETE | Delete user with audit log + last-admin guard (v7.4.1+) |
| `/auth/admin/users/<id>/toggle-admin` | POST | Grant/revoke admin (legacy) |
| `/auth/admin/users/<id>/toggle-download` | POST | Grant/revoke download permission (legacy) |
| `/auth/admin/users/audit-log` | GET | Paginated audit log with action filter (v7.4.1+) |
| `/auth/admin/access-requests` | GET | List pending access requests |
| `/auth/admin/access-requests/<id>/approve` | POST | Approve access request |
| `/auth/admin/access-requests/<id>/deny` | POST | Deny access request |
| `/auth/admin/notifications` | GET/POST | List or create notifications |
| `/auth/admin/inbox` | GET | List user messages |

> **Note**: All `/auth/admin/*` endpoints require the requesting user to have `is_admin=true`.
> Non-admin users receive 403 Forbidden.

### Query Parameters for `/api/audiobooks`

- `page` - Page number (default: 1)
- `per_page` - Items per page (default: 50, max: 200)
- `search` - Full-text search query
- `author` - Filter by author name
- `narrator` - Filter by narrator name
- `collection` - Filter by collection slug (e.g., `fiction`, `mystery-thriller`, `great-courses`)
- `sort` - Sort field (title, author, author_last, narrator_last, duration_hours, acquired_date, published_year, series, edition, author_grouped, narrator_grouped)
- `order` - Sort order (asc, desc)

## Database Schema

The SQLite database stores audiobook metadata with the following key fields:

| Field | Type | Description |
|-------|------|-------------|
| `id` | INTEGER | Primary key |
| `title` | TEXT | Audiobook title |
| `author` | TEXT | Full author name |
| `author_last_name` | TEXT | Extracted last name for sorting |
| `author_first_name` | TEXT | Extracted first name for sorting |
| `narrator` | TEXT | Full narrator name(s) |
| `narrator_last_name` | TEXT | Extracted last name for sorting |
| `narrator_first_name` | TEXT | Extracted first name for sorting |
| `series` | TEXT | Series name (if part of series) |
| `series_sequence` | REAL | Book number in series (e.g., 1.0, 2.5) |
| `edition` | TEXT | Edition info (e.g., "20th Anniversary Edition") |
| `duration_hours` | REAL | Duration in hours |
| `published_year` | INTEGER | Year of publication |
| `acquired_date` | TEXT | Date added to library (YYYY-MM-DD) |
| `file_path` | TEXT | Full path to audio file |
| `file_size_mb` | REAL | File size in megabytes |
| `sha256_hash` | TEXT | SHA-256 hash for duplicate detection |
| `cover_path` | TEXT | Path to extracted cover art |
| `asin` | TEXT | Amazon Standard Identification Number |
| `isbn` | TEXT | International Standard Book Number |
| `source` | TEXT | Audiobook source (audible, google_play, librivox, chirp, etc.) |
| `content_type` | TEXT | Audible content classification (Product, Podcast, Lecture, etc.) |

Additional tables: `supplements` (PDF attachments), `audiobook_genres`, `audiobook_topics`, `audiobook_eras`, `playback_history`

### Normalized Author/Narrator Tables (v7.0+)

| Table | Purpose |
|-------|---------|
| `authors` | Individual author entities with `name` and `sort_name` (last-name-first) |
| `narrators` | Individual narrator entities with `name` and `sort_name` |
| `book_authors` | Many-to-many junction: `audiobook_id` + `author_id` with `position` ordering |
| `book_narrators` | Many-to-many junction: `audiobook_id` + `narrator_id` with `position` ordering |

These tables enable proper multi-author/narrator handling. Books with multiple authors (e.g., "Stephen King, Peter Straub") have each name as a separate entity, enabling per-author grouping, sorting by last name, and admin correction (rename, merge, reassign).

Additional views: `library_audiobooks` (filters to standard audiobook content types)

### Per-User State Tables (Auth Database)

The encrypted auth database (SQLCipher) stores per-user state:

| Table | Purpose |
|-------|---------|
| `user_listening_history` | Session-level listening records with start/end positions and duration |
| `user_downloads` | Download completions with timestamps and format |
| `user_preferences` | User settings including `new_books_seen_at` for new-books marquee |

## Docker — Standalone Container (macOS, Windows, Linux)

The Docker container is a **fully self-contained, standalone product** designed for portability and cross-platform deployment. It includes all databases, dependencies, and runtime components needed to function entirely by itself — no external services, no host dependencies, no native install required.

**Why Docker?**

- **Cross-platform**: Run on macOS, Windows, or any Linux distribution without compatibility concerns
- **Cross-architecture**: Supports amd64 and arm64 (Apple Silicon, Raspberry Pi 3/4/5, and other 64-bit ARM devices)
- **Zero setup**: All dependencies (Python, ffmpeg, SQLCipher, TLS) are bundled inside the container
- **Isolation**: The container runs as a non-root user with no access to the host system beyond mounted volumes
- **Portable**: Move your library to any machine by copying your audiobooks and the Docker volume

The container automatically initializes the database on first run — just mount your audiobooks and start.

### Quick Start (Recommended)

```bash
# Pull and run with a single command
docker run -d \
  --name audiobooks \
  -p 8443:8443 \
  -p 8080:8080 \
  -v /path/to/your/audiobooks:/audiobooks:ro \
  -v audiobooks_data:/app/data \
  -v audiobooks_covers:/app/covers \
  ghcr.io/theboscoclub/Audiobook-Manager:latest

# Access the web interface
open https://localhost:8443
```

On first run, the container automatically:

1. Detects mounted audiobooks
2. Scans and indexes your library
3. Imports metadata into the database
4. Starts the web and API servers

### Using Docker Compose

```bash
# Set your audiobooks directory
export AUDIOBOOK_DIR=/path/to/your/audiobooks

# Optional: Set supplements directory for PDFs
export SUPPLEMENTS_DIR=/path/to/supplements

# Build and run
docker-compose up -d

# Access the web interface
open https://localhost:8443
```

### Build Locally

```bash
# Build the image
docker build -t audiobooks .

# Run with your audiobook directory
docker run -d \
  --name audiobooks \
  -p 8443:8443 \
  -p 8080:8080 \
  -v /path/to/audiobooks:/audiobooks:ro \
  -v audiobooks_data:/app/data \
  -v audiobooks_covers:/app/covers \
  audiobooks
```

### Docker Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `AUDIOBOOK_DIR` | `/audiobooks` | Path to audiobooks inside container |
| `DATABASE_PATH` | `/app/data/audiobooks.db` | SQLite database path |
| `COVER_DIR` | `/app/covers` | Cover art cache directory |
| `SUPPLEMENTS_DIR` | `/supplements` | PDF supplements directory |
| `WEB_PORT` | `8443` | HTTPS web interface port |
| `API_PORT` | `5001` | REST API port |

### Docker Volumes

| Volume | Purpose |
|--------|---------|
| `audiobooks_data` | Persists SQLite database across container restarts |
| `audiobooks_covers` | Persists cover art cache |

### Manual Library Management

If you need to manually rescan or update your library:

```bash
# Rescan audiobook directory
docker exec -it audiobooks python3 /app/scanner/scan_audiobooks.py

# Re-import to database
docker exec -it audiobooks python3 /app/backend/import_to_db.py

# View README inside container
docker exec -it audiobooks cat /app/README.md
```

### Docker Health Check

The container includes a health check that verifies the API is responding:

```bash
# Check container health
docker inspect --format='{{.State.Health.Status}}' audiobooks
```

### Troubleshooting Docker

```bash
# View container logs
docker logs audiobooks

# Check running processes
docker exec -it audiobooks ps aux

# Access container shell
docker exec -it audiobooks /bin/bash

# Restart container (re-runs initialization)
docker restart audiobooks
```

## Requirements (native install)

### System Requirements

| Resource | Minimum | Recommended | Notes |
|----------|---------|-------------|-------|
| Disk | 17 GB | 50+ GB | OS + deps + app; audiobook storage is additional |
| RAM | 2 GB | 4 GB | API uses ~200 MB; more for concurrent users |
| tmpfs `/tmp` | 3 GB | 4 GB | Deployment staging + audio conversion |

### Software Dependencies

- Python 3.12+ (3.14 recommended)
- ffmpeg 7.0+ (with ffprobe)
- Flask (CORS handled natively since v3.2.0)
- openssl (for SSL certificate generation)
- SQLCipher (for encrypted auth database, v5.0+)
- pysqlcipher3 (Python bindings for SQLCipher)
- webauthn >= 2.0 (for Passkey/FIDO2 auth, v5.0+; pip package name: `webauthn`)
- pyotp (for TOTP auth, v5.0+)

### First-time setup

```bash
# Create virtual environment and install dependencies
cd library
python3 -m venv venv
source venv/bin/activate
pip install flask

# Scan your audiobooks
cd scanner
python3 scan_audiobooks.py

# Import to database
cd ../backend
python3 import_to_db.py
```

## Systemd Services

All services use the `audiobook-*` naming convention for easy management.

### Core Services

| Service | Description | Type |
|---------|-------------|------|
| `audiobook-api` | Flask REST API (Gunicorn+geventwebsocket) on localhost:5001 | always running |
| `audiobook-proxy` | HTTPS reverse proxy on 0.0.0.0:8443 | always running |
| `audiobook-redirect` | HTTP to HTTPS redirect on 0.0.0.0:8080 | always running |
| `audiobook-converter` | AAXC → OPUS conversion | always running |
| `audiobook-mover` | Move converted files from tmpfs to storage | always running |
| `audiobook-scheduler` | Maintenance task scheduler daemon (croniter-based) | always running |
| `audiobook-downloader.timer` | Download new Audible audiobooks (every 4h) | timer |
| `audiobook-shutdown-saver` | Save staging files before shutdown | on shutdown |
| `audiobook-upgrade-helper.path` | Watch for upgrade trigger files | path watcher |

### System Services (Recommended)

System services run at boot without requiring login. The installer automatically enables all services.

#### The `audiobook.target` Unit

All audiobook services are grouped under `audiobook.target`, allowing you to control them all with a single command:

```bash
# Start ALL audiobook services at once
sudo systemctl start audiobook.target

# Stop ALL audiobook services at once
sudo systemctl stop audiobook.target

# Restart ALL audiobook services at once
sudo systemctl restart audiobook.target

# Check status of the target (shows all member services)
sudo systemctl status audiobook.target
```

#### Individual Service Management

You can also manage individual services when needed:

```bash
# Check all audiobook services
sudo systemctl status 'audiobook-*'

# Restart just the API server
sudo systemctl restart audiobook-api

# View logs for a specific service
journalctl -u audiobook-api -f

# View all audiobook service logs since today
journalctl -u 'audiobook-*' --since today
```

#### Services Included in `audiobook.target`

| Service | Purpose |
|---------|---------|
| `audiobook-api` | REST API backend (port 5001) |
| `audiobook-proxy` | HTTPS reverse proxy (port 8443) |
| `audiobook-redirect` | HTTP to HTTPS redirect (port 8080) |
| `audiobook-converter` | Continuous AAXC → Opus conversion |
| `audiobook-mover` | Moves converted files to library |
| `audiobook-scheduler` | Maintenance task scheduler daemon |
| `audiobook-downloader.timer` | Scheduled Audible downloads |

### Conversion Priority

The converter service runs with low CPU and I/O priority to avoid impacting interactive use:

- **CPU**: `nice -n 19` (lowest priority)
- **I/O**: `ionice -c 2 -n 7` (best-effort, lowest priority within class)

This ensures audiobook conversion happens in the background without affecting system responsiveness.

### HDD and Network Storage Considerations

If your audiobook library is stored on HDDs, NAS, or network mounts that may not be immediately available at boot, you need to configure the services to wait for those mounts.

**Symptom:** Services fail at boot with errors like:

```text
Failed at step NAMESPACE spawning /bin/sh: No such file or directory
audiobook-api.service: Failed with result 'exit-code'.
```

The service typically recovers after a few restart attempts (once the mount is ready), but this can be fixed properly.

**Solution:** Edit the service file to include your data path in `RequiresMountsFor`:

```bash
# Edit the API service
sudo systemctl edit --full audiobook-api.service
```

In the `[Unit]` section, add your data path to `RequiresMountsFor`:

```ini
[Unit]
# ... existing directives ...
# Add your data mount path alongside /opt/audiobooks
RequiresMountsFor=/opt/audiobooks /path/to/your/audiobooks
```

**Common scenarios:**

| Storage Type | Example Path | Notes |
|--------------|--------------|-------|
| Secondary HDD | `/mnt/data/Audiobooks` | Add to RequiresMountsFor |
| BTRFS subvolume | `/hddRaid1/Audiobooks` | Add to RequiresMountsFor |
| NFS mount | `/mnt/nas/audiobooks` | Also add `After=remote-fs.target` |
| CIFS/SMB mount | `/mnt/share/audiobooks` | Also add `After=remote-fs.target` |

**For network mounts**, also add network dependencies:

```ini
[Unit]
After=network-online.target remote-fs.target
Wants=network-online.target
RequiresMountsFor=/opt/audiobooks /mnt/nas/audiobooks
```

After editing, reload and restart:

```bash
sudo systemctl daemon-reload
sudo systemctl restart audiobook-api.service
```

**Why this happens:** The `audiobook-api` service uses `ProtectSystem=strict` for security hardening, which requires all paths in `ReadWritePaths` to be available when setting up the service's filesystem namespace. If your data path uses `nofail` mount options (common for non-critical mounts), systemd won't wait for it by default.

## Acknowledgments

This project would not be possible without the incredible work of many developers and open-source communities. I am deeply grateful to:

### Core Dependencies

- **[KrumpetPirate](https://github.com/KrumpetPirate)** and the **55+ contributors** to [AAXtoMP3](https://github.com/KrumpetPirate/AAXtoMP3) - The foundation of the converter component. Years of community effort went into building this essential tool for the audiobook community.

- **[mkb79](https://github.com/mkb79)** for [audible-cli](https://github.com/mkb79/audible-cli) - An indispensable CLI tool for interacting with Audible's API, downloading books, and extracting metadata. This project relies heavily on audible-cli for AAXC decryption and metadata.

- **[FFmpeg](https://ffmpeg.org/)** - The Swiss Army knife of multimedia processing. FFmpeg handles all audio conversion, metadata extraction, and stream processing in this project.

- **[Flask](https://flask.palletsprojects.com/)** by the Pallets Projects team - The lightweight Python web framework powering the REST API.

- **[SQLite](https://sqlite.org/)** - The embedded database engine that stores and indexes the audiobook library with remarkable efficiency.

- **[mutagen](https://mutagen.readthedocs.io/)** - Python library for handling audio metadata, essential for embedding cover art in Opus files.

### Development Tools

- **[Claude Code](https://claude.ai/code)** (Anthropic) - AI coding assistant that helped with implementation details, debugging, and documentation throughout development.

- **[CachyOS](https://cachyos.org/)** - The Arch-based Linux distribution where this project was developed and tested. CachyOS provides an excellent development environment with up-to-date packages and performance optimizations.

### The Audiobook Community

Special thanks to the broader audiobook and self-hosting communities on Reddit ([r/audiobooks](https://www.reddit.com/r/audiobooks/), [r/selfhosted](https://www.reddit.com/r/selfhosted/)) and various forums for sharing knowledge, workarounds, and inspiration for managing personal audiobook libraries.

---

*This project is a personal tool shared in the hope that others might find it useful. All credit for the underlying technologies belongs to their respective creators and communities.*

## Changelog

### v6.6.6

- **Auth**: Username limits changed from 5-16 to 3-24 characters
- **Shell**: All scripts reverted to bash; zsh syntax replaced with bash equivalents
- **CI**: ShellCheck linting added to GitHub Actions
- Multiple systemd, converter, and installer fixes
- See [CHANGELOG.md](CHANGELOG.md) for full details

### v6.6.5.1

- **Auth**: Unified all invitation expiry to 48 hours (TOTP, passkey, and magic link invitations)
- See [CHANGELOG.md](CHANGELOG.md) for full details

### v6.6.5

- **Cover Art**: Standalone cover recovery — recovers 645 missing covers from standalone `.jpg` files extracted during conversion
- **Converter**: `embed_ogg_cover()` now uses venv Python (with mutagen) instead of bare `python3`
- **Library**: Lectures and Great Courses hidden from main library, visible only through dedicated collections
- **Collections**: New "Lectures" collection; Great Courses and Podcasts & Shows collections updated
- See [CHANGELOG.md](CHANGELOG.md) for full details

### v6.6.4

- **Fix**: Bash/zsh compatibility for config loader — scripts with `set -u` no longer fail on `${0:A:h}`
- **Fix**: Service stability — removed vestigial ReadWritePaths causing 230+ namespace failures; added restart rate limiting
- **Fix**: Queue builder prefix matching — "trial" no longer false-matches "trials of koli"
- **Converter**: Only processes DRM-encrypted formats (AAXC/AAX/AA); playable formats skipped
- **Podcasts & Shows**: New collection with `bypasses_filter` for non-audiobook content types
- See [CHANGELOG.md](CHANGELOG.md) for full details

### v6.6.3

- **Collections**: Restructured from flat list to hierarchical tree with 18 top-level genres and 35 collapsible subgenres
- **UI**: Fluid responsive scaling with CSS `clamp()` across all CSS files — eliminates layout jumps between breakpoints
- **Deploy**: Consolidated deploy.sh + deploy-vm.sh into upgrade.sh with `--remote`, `--user`, `--yes` flags
- See [CHANGELOG.md](CHANGELOG.md) for full details

### v6.6.2.6

- **Deploy**: Fixed venv creation using pyenv shim — symlinks into `/home/` inaccessible under systemd `ProtectHome=yes`; now uses system Python (`/usr/bin/python3.14`)
- **Deploy**: Added `--exclude='venv'` to `deploy-vm.sh` rsync to prevent overwriting production venvs
- **Upgrade**: Added post-upgrade venv health check to `upgrade.sh` — detects broken symlinks, recreates with system Python
- See [CHANGELOG.md](CHANGELOG.md) for full details

### v6.6.2.5

- **Collections**: Fixed historical-fiction and action-adventure collections returning wrong book counts
- **Tests**: Updated schema version assertions to reflect migration 006 (webauthn_credentials table)
- See [CHANGELOG.md](CHANGELOG.md) for full details

### v6.6.2.4

- **Auth**: Added `safeJsonParse()` to all 8 auth HTML pages — handles HTML error responses gracefully
- **Auth**: Added missing `webauthn_credentials` table to schema.sql and migration 006
- See [CHANGELOG.md](CHANGELOG.md) for full details

### v6.6.2.3

- **Web UI**: Added cache-busting version params to all `<script>`, `<link>`, and CSS `@import` across all 12 HTML files
- **Web UI**: Fixed user dropdown menu extending beyond left browser edge
- **Web UI**: Added null guards to `escapeHtml()`, `selectAuthor()`, `selectNarrator()` in library.js
- See [CHANGELOG.md](CHANGELOG.md) for full details

### v6.6.2.2

- **Uninstall**: Comprehensive `uninstall.sh` with dynamic discovery of all installation artifacts
- **Install**: zsh reserved variable bugs fixed — `local path=` corrupts `$PATH`, `local status=` fails (read-only)
- See [CHANGELOG.md](CHANGELOG.md) for full details

### v6.6.2.1

- **Upgrade**: `--force` flag for `upgrade.sh` to allow same-version reinstall
- See [CHANGELOG.md](CHANGELOG.md) for full details

### v6.6.2

- **Auth**: Magic link UX overhaul — admin invite defaults to magic link, auto-fill claim page from URL params
- **UI**: Mobile responsive utilities — horizontal scroll tabs, iOS auto-zoom prevention
- See [CHANGELOG.md](CHANGELOG.md) for full details

### v6.6.1

- **Security**: HTTP security headers (CSP, HSTS, X-Frame-Options, X-Content-Type-Options, Referrer-Policy, Permissions-Policy) on all API responses
- **Security**: Session cookies hardened (Secure, HttpOnly, SameSite=Lax)
- **Security**: Patched CVE-2025-43859 (h11 HTTP request smuggling)
- **Security**: `NoNewPrivileges=yes` enforced in upgrade-helper service
- **Fix**: tmpfiles.conf source path corrected in install.sh and upgrade.sh (fixes /tmp directories not recreated on reboot)
- **Fix**: .dockerignore glob patterns fixed to exclude Python bytecode in all subdirectories
- **CI**: Python upgraded from 3.11 to 3.14 in ci.yml
- See [CHANGELOG.md](CHANGELOG.md) for full details

### v6.6.0

- **Scripts**: Eliminated script drift — replaced stale full copies in `/usr/local/bin/` with symlinks to canonical scripts
- **Deploy**: Added `refresh_bin_symlinks()` and SCRIPT_ALIASES map across all install/deploy/upgrade entry points
- See [CHANGELOG.md](CHANGELOG.md) for full details

### v6.3.0

- **Per-User State**: Listening history, download tracking, and user preferences with encrypted storage
- **UI**: My Library tab, new-books marquee, About page, activity audit in Back Office, genre management in Bulk Ops
- **API**: 11 new endpoints for user state, genre management, and admin activity audit
- **Position Sync**: Replaced Audible cloud dependency with self-contained per-user local tracking
- See [CHANGELOG.md](CHANGELOG.md) for full details

### v6.2.0

- **Security**: FLASK_DEBUG default false, USE_WAITRESS default true, CORS credentials header, admin_or_localhost on upgrade check
- **Infrastructure**: systemd service wrapper names match installed scripts, Dockerfile HEALTHCHECK uses /api/system/health
- **Quality**: Shell formatting (shfmt), ruff format, YAML lint fixes, hardcoded path elimination
- **Feature**: Health endpoint (`/api/system/health`), Help system with interactive tutorial, Back Office visibility fix
- See [CHANGELOG.md](CHANGELOG.md) for full details

### v6.1.3

- **Fix**: Rewrite invite flow — eliminates "credentials already claimed" and method selection loop bugs during claim
- **Fix**: Download toggle button now calls correct API endpoint
- **Fix**: Library rescan progress meter shows real-time updates (ANSI escape code stripping)
- See [CHANGELOG.md](CHANGELOG.md) for full details

### v6.1.2.1

- **Admin**: Invite User button for pre-registering and approving new users with claim token workflow
- See [CHANGELOG.md](CHANGELOG.md) for full details

### v6.1.2

- **Fix**: First-user registration returned backup codes as string instead of JSON array (caused JavaScript TypeError)
- **Fix**: Proxy HTTP error handler forwards Flask's original response body
- See [CHANGELOG.md](CHANGELOG.md) for full details

### v6.1.1

- **Scripts**: Comprehensive bash-to-zsh compatibility fixes across all shell scripts
- **CI**: Track `library/auth/schema.sql` in git; fix ruff linting in CI
- See [CHANGELOG.md](CHANGELOG.md) for full details

### v6.1.0

- **UI**: Comprehensive responsive design — mobile/desktop, portrait/landscape, zoom/pinch
- **UI**: Touch-aware interactions, safe area insets, reduced motion support
- **Fix**: Install/upgrade separation checks use dynamic paths
- See [CHANGELOG.md](CHANGELOG.md) for full details

### v6.0.0

- **Security**: Dual-mode security architecture — admin endpoints adapt protection based on deployment mode
- **Install**: Service account creation, auth key generation, DB initialization, venv validation
- **BREAKING**: All 27 shell scripts converted from bash to zsh
- **Deps**: Fix HIGH CVEs in pillow (12.1.1) and cryptography (46.0.5)
- See [CHANGELOG.md](CHANGELOG.md) for full details

### v5.0.2

- **Testing**: VM_TESTS environment variable for WebAuthn origin selection
- **API**: Use sys.executable for venv compatibility in subprocess calls
- **Deploy**: Add library/scripts/ and library/common.py to VM deployment sync
- **Security**: Explicit permissions blocks for all GitHub Actions workflow jobs

### v5.0.1.1

- **Cleanup**: Remove all remaining periodicals code, services, and references
- **Systemd**: Fix boot failures caused by symlink resolution in ProtectSystem=strict namespaces
- **Systemd**: Fix stale symlinks with wrong "audiobooks-" prefix (should be "audiobook-")
- **Systemd**: Update ExecStartPre checks from lsof to ss (iproute2)

### v5.0.1

- **Proxy**: Route `/auth/*` endpoints through HTTPS reverse proxy to Flask backend
- **Proxy**: Forward `Cookie` header for session-based authentication
- **Docs**: Updated all project documentation for v5.0.0 authentication release

### v5.0.0

- **Authentication**: Multi-user auth system with TOTP, Passkey (WebAuthn), and FIDO2 hardware key support
- **Authentication**: SQLCipher encrypted auth database (AES-256 at rest)
- **Authentication**: Admin approval flow with claim token system for new user registration
- **Authentication**: Backup code recovery, session management, per-user playback positions
- **Web UI**: Login page, claim page, admin panel, contact/notification system
- **API**: All endpoints auth-gated when AUTH_ENABLED=true
- **BREAKING**: Unauthenticated API requests return 401 when auth is enabled

### v4.1.2

- **Web UI**: "Check for Updates" button in Utilities page
- **Upgrade**: Fixed multi-installation detection for `--from-github` and `--from-project`

### v4.0.0

- **BREAKING: Periodicals Feature Removed**: The "Reading Room" periodicals subsystem (podcasts, newspapers, meditation) has been extracted to a separate R&D branch (`feature/periodicals-rnd`). This simplifies the main codebase to focus on audiobooks only.
  - Migration `010_drop_periodicals.sql` removes periodicals tables
  - To restore periodicals, use tag `v3.11.2-with-periodicals`

### v3.11.2

- **Podcast Episode Download & Conversion**: Full support for downloading and converting podcast episodes from Audible
- **Periodicals Orphan Detection**: Find and delete episodes whose parent series no longer exists
- **Security Fixes**: SQL injection prevention, log injection fixes, XSS prevention in library.js
- **Periodicals SSE Fix**: Fixed Flask request context issue in SSE generator
- **Build Queue Fix**: Fixed to only process AAX/AAXC files, not MP3 podcasts

### v3.11.1

- **Deploy Fix**: Fixed `deploy.sh` to include root-level management scripts (`upgrade.sh`, `migrate-api.sh`) that were being silently skipped during deployment

### v3.11.0

- **Periodicals Sorting**: Reading Room supports title, date, subscription, and download status sorting
- **Whispersync Position Sync**: Periodicals now sync listening positions with Audible
- **Auto-Download**: Subscribed podcast series automatically queue new episodes
- **Podcast Expungement**: Complete removal of unsubscribed podcast content
- **Test Fixes**: Resolved 19 test failures, improved code quality

### v3.10.1

- **Architecture Documentation**: Comprehensive ARCHITECTURE.md update with Scanner Module, API Module, Systemd Services, and Scripts Reference sections
- **Periodicals Sync**: Enhanced parent/child hierarchy support for podcast episodes
- **Hardcoded Paths Fix**: Fixed 2 hardcoded paths in shell scripts, removed invalid inline comments from systemd files

### v3.10.0

- **BREAKING: Naming Convention Standardization**: All service names, CLI commands, and config files now use singular "audiobook-" prefix instead of plural "audiobooks-" to align with project name
- **Status Script Enhancement**: `audiobook-status` now displays services and timers in separate sections
- **Documentation Dates**: Updated last-modified dates in ARCHITECTURE.md and POSITION_SYNC.md

### v3.9.8

- **Major Refactoring**: Split monolithic `utilities_ops.py` (994 lines) into modular package with 5 focused modules
- **Test Coverage**: Added 27 new test files, increased coverage from 77% to 85%
- **Code Quality**: Removed unused imports, fixed incorrect default paths

### v3.9.7.1

- **Audit Fixes**: PIL rebuilt for Python 3.14, flask-cors removed from install scripts, systemd ConditionPathExists paths fixed

### v3.9.7

- **Upgrade Script Path Bug**: Fixed `upgrade-helper-process` referencing wrong path (was `/opt/audiobooks/upgrade.sh`, now `/opt/audiobooks/scripts/upgrade.sh`)
- **Duplicate Finder Endpoint**: Fixed JavaScript calling non-existent `/api/duplicates/by-hash` (now `/api/duplicates`)
- **Upgrade Script Sync**: Root-level management scripts now properly sync during upgrades

### v3.9.6

- **Security Hardening**: Fix CVE-2025-43859 (h11 HTTP smuggling), enforce TLS 1.2 minimum, add SSRF path validation
- **CodeQL Remediation**: Fix 30 code scanning alerts (stack trace exposure, empty except handlers, type errors)
- **Code Quality**: Fix ruff linting errors, add missing type imports, improve error logging

### v3.9.5.1

- **Version Badges**: Multi-segment version badges with hierarchical color scheme
- **Documentation**: Version history table showing release progression

### v3.9.5

- **Schema Tracking**: Database schema now tracked in git (schema.sql)
- **Content Filter**: Expanded AUDIOBOOK_FILTER to include Lecture, Performance, Speech types
- **Reliability**: Prevent concurrent queue rebuild processes with flock
- **Scripts**: Fixed shellcheck warnings in build scripts

### v3.9.4

- **Security**: Replace insecure mktemp() with mkstemp() for temp file creation
- **Reliability**: Add signal trap to converter script for clean FFmpeg shutdown
- **Code Quality**: Fix missing imports, remove unused variables, add exception logging

### v3.9.3

- **Periodicals (Reading Room)**: Simplified to flat data schema with skip list support
- **Mover Service**: Fixed process stampede with flock wrapper

### v3.9.0

- **Periodicals "Reading Room"**: New subsystem for Audible episodic content
  - Manages podcasts, newspapers, meditation series separately from main library
  - Real-time sync status via Server-Sent Events (SSE)
  - Individual or bulk episode download queuing
  - Twice-daily auto-sync via systemd timer (06:00 and 18:00)
- **Security Fixes**: Patched CVE-2026-21441 (urllib3), CVE-2025-43859 (h11)
- **Code Cleanup**: Removed deprecated Flask-CORS, dead CSS code

### v3.8.0

- **Position Sync with Audible**: Bidirectional playback position synchronization
  - "Furthest ahead wins" conflict resolution - you never lose progress
  - Seamlessly switch between Audible apps and self-hosted library
  - Web player auto-saves every 15 seconds to both localStorage and API
  - Batch sync all books with ASINs in a single operation
- **Comprehensive Documentation**: New `docs/POSITION_SYNC.md` with setup guides, API reference, troubleshooting

### v3.7.2

- **Position Sync API**: Bidirectional playback position synchronization with Audible cloud
  - Sync single books or batch sync all audiobooks with ASINs
  - "Furthest ahead wins" logic for conflict resolution
  - Position history tracking
- **Bug Fixes**: Service timer control, download path, database vacuum improvements

### v3.7.0

- **Upgrade System**: Fixed non-interactive upgrade failures in systemd service
  - Fixed bash arithmetic causing exit code 1 with `set -e`
  - Auto-confirm prompts when triggered from web UI
- **UI**: Changed dark green text to cream-light for better contrast

### v3.6.x

- **Security**: Privilege-separated helper service for system operations
  - API now runs with `NoNewPrivileges=yes` security hardening
  - Service control and upgrades work via file-based IPC with helper service
- **System Administration API**: New `/api/system/*` endpoints for service control and upgrades
- **Web UI**: Back Office can now start/stop/restart services and trigger upgrades
- **Fixes**: Service control from web UI, upgrade from web UI, race conditions

### v3.5.x ⚠️ END OF LIFE
>
> **No longer supported.** Upgrade to v3.7.0 or later immediately.
> No security patches or updates will be released for 3.5.x.

- **Checksum Tracking**: MD5 checksums (first 1MB) generated automatically during download and move operations for fast duplicate detection
- **Generate Checksums**: New Utilities button to regenerate all checksums for Sources (.aaxc) and Library (.opus) files
- **Index Cleanup**: `cleanup-stale-indexes` script removes entries for deleted files from all indexes; automatic cleanup on file deletion
- **Bulk Operations Redesign**: Clear step-by-step workflow (Filter → Select → Act) with explanatory intro, descriptive filter options, and use-case examples
- **Conversion Queue**: Hybrid ASIN + title matching for accurate queue building, real-time index updates after each conversion
- **UI Streamlining**: Removed redundant Audiobooks tab from Back Office (search available on main page)
- **Fixes**: Queue builder robustness, mover timing optimization, version display

### v3.4.2

- **Refactoring**: Split utilities.py (1067 lines) into 4 focused sub-modules with reduced complexity
- **Scanner**: New shared `metadata_utils.py` module, complexity D(24) → A(3)
- **Quality**: Average cyclomatic complexity reduced from D to A (3.7)
- **Fixes**: Conversion progress accuracy, queue count sync, code cleanup

### v3.4.1

- **Architecture**: Comprehensive ARCHITECTURE.md guide with install/upgrade/migrate workflows
- **Install**: Fixed to use `/opt/audiobooks` as canonical location with auto-service start
- **Migrate**: Added service stop/start lifecycle to `migrate-api.sh`
- **Symlinks**: Wrapper scripts now source from canonical `/opt/audiobooks/lib/` path

### v3.4.0

- **Collections**: Per-job conversion stats, sortable active conversions, text-search based genres
- **Config**: Fixed critical DATA_DIR config reading issue
- **Covers**: Cover art now stored in data directory (`${AUDIOBOOKS_DATA}/.covers`)

### v3.3.x

- **Conversion Monitor**: Real-time progress bar, rate calculation, ETA in Back Office
- **Upgrade**: Auto stop/start services during upgrade

### v3.2.1

- **Docker Build**: Added Docker build job to release workflow for automated container builds
- **Performance**: Increased default parallel conversion jobs from 8 to 12
- **Cleanup**: Removed redundant config fallbacks from scripts (single source of truth)

### v3.2.0

- **GitHub Releases**: Standalone installation via `bootstrap-install.sh`
- **Upgrade System**: GitHub-based upgrades with `audiobook-upgrade --from-github`
- **Release Automation**: CI/CD workflow and release tarball builder
- **Repository Renamed**: `audiobook-toolkit` → `Audiobook-Manager`
- **Removed Flask-CORS**: CORS now handled natively by the application
- **Cleanup**: Removed legacy `api.py` (2,244 lines) and `web.legacy/` directory
- **Security**: Fixed SQL injection in `generate_hashes.py`, Flask blueprint registration

### v3.1.1

- **Fix**: RuntimeDirectoryMode changed from 0755 to 0775 for group write access

### v3.1.0

- **Install Manifest**: `install-manifest.json` for production validation
- **API Migration**: Tools for switching between monolithic and modular architectures
- **Modular API**: Flask Blueprint architecture (`api_modular/`)
- **Testing**: Fixed 7 hanging tests, resolved mock path issues
- **Quality**: Fixed 13 shellcheck warnings, 18 mypy type errors

### v3.0.5

- **Security**: SQL injection fix in genre queries, non-root Docker user
- **Docker**: Pinned base image to `python:3.11.11-slim`
- **Ports**: Standardized to 8443 (HTTPS), 8080 (HTTP redirect)
- **Documentation**: Added LICENSE, CONTRIBUTING.md, CHANGELOG.md

### v3.0.0

- **The Back Office**: New utilities page with vintage library back-office aesthetic
  - Database management: stats, vacuum, rescan, reimport, export (JSON/CSV/SQLite)
  - Metadata editing: search, view, and edit audiobook metadata
  - Duplicate management: find and remove duplicates by title/author or SHA-256 hash
  - Bulk operations: select multiple audiobooks, bulk update fields, bulk delete
- **API Enhancements**: PUT/DELETE endpoints for editing, storage size and database size in stats
- **Smart Author/Narrator Sorting**: Sort by last name, first name
  - Single author: "Stephen King" → sorts as "King, Stephen"
  - Co-authored: "Stephen King, Peter Straub" → appears in both K and S letter groups
  - Anthologies: "Gaiman (contributor), Martin (editor)" → sorts by editor (Martin)
  - Role suffixes stripped: "(editor)", "(translator)", "- editor" handled correctly
- **Proxy Server**: Added PUT/DELETE method support for utilities operations
- **Removed**: Find Duplicates dropdown from main Library page (moved to Back Office)

### v2.9

- **Metadata Preservation**: Import now preserves manually-populated narrator and genre data from Audible exports, preventing data loss on reimport
- **Improved Deduplication**: Scanner now intelligently deduplicates between main library and `/Library/Audiobook/` folder, preferring main library files while keeping unique entries
- **Security**: Updated flask-cors from 4.0.0 to 6.0.0 (fixes CVE-2024-6839, CVE-2024-6844, CVE-2024-6866)

### v2.8

- Multi-source audiobook support (Google Play, Librivox, OpenLibrary)
- Parallel SHA-256 hash generation (24x speedup on multi-core systems)
- Automatic hashing during import
- New `isbn` and `source` database fields

### v2.7

- Collections sidebar for browsing by category
- Genre sync from Audible library export

### v2.6

- Author/narrator autocomplete with letter group filters
- Enhanced sorting options (first/last name, series sequence, edition)
- Narrator metadata sync from Audible

### v2.5

- Docker auto-initialization
- Portable configuration system
- Production-ready HTTPS server with Gunicorn+geventwebsocket and real-time WebSocket support

See [GitHub Releases](https://github.com/TheBoscoClub/Audiobook-Manager/releases) for full version history.

## Known Issues

| Issue | Workaround | Status |
|-------|------------|--------|
| Browser security warning for self-signed SSL cert | Click "Advanced" → "Proceed to localhost" | By design |
| Narrator/genre data must be re-synced after adding new books | Run `update_narrators_from_audible.py` and `populate_genres.py` after importing | Planned: Auto-sync on import |
| ~No UI for duplicate management~ | ~~Use CLI scripts~~ | ✅ Fixed in v3.0 (Back Office) |
| ~Limited metadata editing in webapp~ | ~~Edit database directly~~ | ✅ Fixed in v3.0 (Back Office) |

## Roadmap

### Completed Milestones

**Secure by Design (v5.0)**

- ~~**Authentication & Authorization**~~: ✅ Multi-user auth with TOTP, Passkey, FIDO2
- ~~**Secrets Management**~~: ✅ SQLCipher encrypted auth database, Fernet-encrypted credentials
- ~~**Audit Logging**~~: ✅ Contact log, access request tracking, session audit trail
- ~~**Input Validation**~~: ✅ Username validation, token sanitization, auth-gated endpoints

**Per-User Experience (v6.3)**

- ~~**My Library**~~: ✅ Personalized library tab with progress bars and listening history
- ~~**Activity Tracking**~~: ✅ Per-user listening history and download tracking
- ~~**New Books**~~: ✅ Art Deco marquee for newly added audiobooks
- ~~**Admin Audit**~~: ✅ Activity audit log with filtering, stats, and top content
- ~~**Genre Management**~~: ✅ Bulk genre add/remove in Back Office
- ~~**About Page**~~: ✅ Credits, attributions, and version display

**Security Hardening (v6.6)**

- ~~**HTTP Security Headers**~~: ✅ CSP, HSTS, X-Frame-Options, X-Content-Type-Options, Referrer-Policy, Permissions-Policy
- ~~**Session Cookie Hardening**~~: ✅ Secure, HttpOnly, SameSite=Lax flags enforced

**Back Office (v3.0–v6.0)**

- ~~**Database Management**~~: ✅ Stats, vacuum, rescan, reimport, export (JSON/CSV/SQLite)
- ~~**Duplicate Management**~~: ✅ Four detection methods (title/author, SHA-256, source checksums, library checksums)
- ~~**Audiobook Management**~~: ✅ Metadata editing, bulk operations, bulk delete
- ~~**Bulk Operations**~~: ✅ Filter → Select → Act workflow with genre management

### Planned Features

**Security Hardening**

- Certificate Authority Integration (Let's Encrypt / trusted CAs)
- Container Hardening (read-only filesystems, non-root execution)
- Rate limiting

**Enhanced Player**

- Chapter navigation
- Bookmarks and notes
- Sleep timer
- Queue/playlist management

**Mobile Support**

- Progressive Web App (PWA) support
- Offline playback caching

### Contributing

Feature requests and pull requests welcome! See the [GitHub Issues](https://github.com/TheBoscoClub/Audiobook-Manager/issues) page.

## License

See individual component licenses in `converter/LICENSE` and `library/` files.
