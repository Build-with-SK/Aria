"""
src/options/
============
Options she can LOOK at. Not options she can trade.

The distinction is the whole package. There is no margin model here, no
assignment handling, no early-exercise logic, and no risk gate that
understands what a short gamma position does to an account overnight. Until
those exist, a chain is research and nothing more — `chain.py` says so in code
rather than in a comment, and nothing in this package can reach a broker.
"""
