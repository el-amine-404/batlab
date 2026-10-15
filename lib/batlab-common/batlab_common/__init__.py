"""Code shared by batlab's own services (songdrop, musicguard): which song a
title names, whether two recordings are one, the stores' 30-second previews,
and notifications through Apprise.

Standard library only, so it runs in any Python 3.11+ environment, spotDL's
included. It needs ffmpeg on the PATH for fingerprints (fpcalc, Chromaprint's
own tool, is used instead when installed: same result, four times faster).
"""

__version__ = "1.0.0"
