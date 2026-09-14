"""Web entry point.

pygbag decides which WebAssembly packages to ship by scanning THIS file's
imports — not the whole project — so pygame is imported explicitly below.
Without it, pygbag ships a pygame stub and the first pygame.init() dies with
"module 'pygame' has no attribute 'init'".

numpy is deliberately NOT imported here. The game only uses it to synthesise
sound effects, it is by far the heaviest package to download, and browsers
gate audio behind a user gesture anyway — so the browser build runs silent.

Running this file directly works too; it is the same game.
"""
import asyncio

import pygame  # noqa: F401  — declared so pygbag bundles it

import spirit_fighters

asyncio.run(spirit_fighters.main())
