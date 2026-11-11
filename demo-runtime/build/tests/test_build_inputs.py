import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from build_native import extract_zip,verify_tree
from build_os import image_from_receipt

class BuildInputTests(unittest.TestCase):
    def test_zip_traversal_rejected_before_extraction(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);z=root/'bad.zip'
            with zipfile.ZipFile(z,'w') as archive:archive.writestr('../escaped','bad')
            with self.assertRaises(ValueError):extract_zip(z,root/'out')
            self.assertFalse((root/'escaped').exists())

    def test_tree_hash_inventory_and_symlinks(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'file').write_bytes(b'x');(root/'link').symlink_to('file')
            records={'file':{'size':1,'sha256':hashlib.sha256(b'x').hexdigest()},'link':{'link':'file'}}
            verify_tree(root,records)
            (root/'file').write_bytes(b'y')
            with self.assertRaises(ValueError):verify_tree(root,records)
            (root/'file').write_bytes(b'x');(root/'extra').write_text('x')
            with self.assertRaises(ValueError):verify_tree(root,records)

    def test_reject_unverified_os_receipt(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'receipt.json').write_text(json.dumps({'target':'cli','passed':False,'image':'anything'}))
            with self.assertRaises(ValueError):image_from_receipt(root,'cli')

if __name__=='__main__':unittest.main()
