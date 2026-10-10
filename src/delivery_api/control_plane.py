"""Delivery-only launcher. No engine recovery, dispatch or provider calls.

This is a container runtime profile, not production launch approval. Existing
cloud identity/RLS still applies. Mutations remain fenced in this profile.
"""
import os


def main():
    os.environ['DELIVERY_CONTROL_PLANE_ONLY']='1'
    from .__main__ import main as serve
    serve()


if __name__=='__main__':main()
