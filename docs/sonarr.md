# Sonarr settings outside recyclarr

recyclarr (`compose/arr/conf/recyclarr/sonarr.yml`) owns quality profiles,
custom formats, quality sizes and naming. These live only in Sonarr's database,
so they are recorded here. All were set on 2026-10-03.

## Recycling bin

Settings → Media Management → Recycling Bin: `/data/recycle`, cleanup after 14
days. The host folder is `/mnt/storage/data/recycle`, on the library's disk. An
upgrade moves the replaced file there instead of deleting it, which is what lets
the [dub keeper](../dubkeeper/README.md) carry a lost dub or subtitles into the
new file, and lets a bad upgrade be undone by hand.

## Anime from groups the guide does not rank

The `Anime` profile accepts only release groups in the guide's tiers (minimum
score 100, the lowest tier's score). Every major simulcast ripper is tiered
(SubsPlease Web Tier 05; Erai-Raws, VARYG and ToonsHub Tier 04), so this blocks
almost nothing new. What it blocks is unreviewed groups: re-encodes, mini
encodes, upscales, hardsubs (often in another language) and fakes.

For a show with no tiered release at all, opt it in, per series:

1. Edit the series → Quality Profile `Anime (any genuine)` (made by recyclarr:
   same tiers and scores, minimum 0).
2. Add the tag `anime-any`. Its delay profile (Settings → Profiles → Delay
   Profiles, order 1) holds a release for 6 hours unless it scores 100 or more,
   so a tiered release arriving in that window is taken instead. "Bypass if
   highest quality" is off, since an unranked Bluray-1080p would otherwise skip
   the wait.

Raws, LQ groups, dubs only, AV1 and VOSTFR stay at -10000 in both profiles. An
unranked file scores 0, so any tiered release replaces it, and an unranked
release can never replace a tiered file: the series can stay opted in.

## Upgrade order

Picture first, dub second: tiers are 100 apart and `Anime Dual Audio` adds 50,
so dual audio only breaks a tie within a tier. Cleanuparr's Seeker keeps
searching until the cutoff (10000), so a dual-audio copy in the same tier as a
Japanese-only file is still picked up later. A tier is the group's reputation,
not a measurement of the release: on 2026-10-02 a Tier 04 BD (UQW) and a Tier 07
BD (iAHD) of *Vinland Saga* were the same disc at SSIM 0.991, and the "upgrade"
would only have lost the English dub.
