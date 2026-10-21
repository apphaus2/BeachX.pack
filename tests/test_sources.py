import unittest

from beachx_pack.sources import padus


KML = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document><Folder>
<Placemark><description><![CDATA[<table><tr><td>Category</td><td>Fee</td></tr>
<tr><td>Unit_Nm</td><td>Test Beach Park</td></tr><tr><td>Pub_Access</td><td>OA</td></tr>
<tr><td>Mang_Type</td><td>LOC</td></tr><tr><td>d_Mang_Typ</td><td></td></tr>
<tr><td>Mang_Name</td><td>CNTY</td></tr><tr><td>d_Mang_Nam</td><td></td></tr>
<tr><td>Des_Tp</td><td>LP</td></tr><tr><td>d_Des_Tp</td><td>Local Park</td></tr>
<tr><td>GIS_Acres</td><td>12</td></tr></table>]]></description>
<MultiGeometry><Polygon><outerBoundaryIs><LinearRing><coordinates>
-157.83,21.27,0 -157.82,21.27,0 -157.82,21.28,0 -157.83,21.28,0 -157.83,21.27,0
</coordinates></LinearRing></outerBoundaryIs><innerBoundaryIs><LinearRing><coordinates>
-157.826,21.274 -157.824,21.274 -157.824,21.276 -157.826,21.276 -157.826,21.274
</coordinates></LinearRing></innerBoundaryIs></Polygon></MultiGeometry></Placemark>
</Folder></Document></kml>"""

DOMAINS = """junk<GPCodedValueDomain2 xsi:type='x'><DomainName>Agency Name</DomainName>
<CodedValues><CodedValue><Name>County Land</Name><Code xsi:type='xs:string'>CNTY</Code></CodedValue>
</CodedValues></GPCodedValueDomain2>junk<GPCodedValueDomain2><DomainName>Agency Type</DomainName>
<CodedValues><CodedValue><Name>Local Government</Name><Code>LOC</Code></CodedValue></CodedValues>
</GPCodedValueDomain2>"""


class PadusTests(unittest.TestCase):
    def test_kml_attributes_decoded_from_domains(self):
        units = padus.read_kml(KML.encode(), padus.read_domains(DOMAINS))
        self.assertEqual(len(units), 1)
        a = units[0]["attrs"]
        self.assertEqual((a["Unit_Nm"], a["MngTp_Desc"], a["MngNm_Desc"], a["DesTp_Desc"]),
                         ("Test Beach Park", "Local Government", "County Land", "Local Park"))
        self.assertEqual(len(units[0]["polygons"][0]), 2)  # outer and one hole

    def test_local_lookup_with_search_radius_and_holes(self):
        index = padus.Index(padus.read_kml(KML.encode()))
        self.assertEqual(len(index.lookup(21.272, -157.828)), 1)  # inside
        self.assertEqual(len(index.lookup(21.275, -157.825)), 0)  # in the hole, > 30 m from edges
        self.assertEqual(len(index.lookup(21.2802, -157.825)), 1)  # 22 m north of the edge
        self.assertEqual(len(index.lookup(21.2810, -157.825)), 0)  # 111 m north
        f = padus.fields({"features": [{"attributes": a} for a in index.lookup(21.272, -157.828)]})
        self.assertEqual((f["public_access"], f["padus_unit_name"]), ("Open", "Test Beach Park"))

    def test_geojson_export_shape(self):
        data = {"features": [{"properties": {"Category": "Fee", "Unit_Nm": "U", "Pub_Access": "RA"},
                              "geometry": {"type": "Polygon", "coordinates": [[
                                  [-157.83, 21.27], [-157.82, 21.27], [-157.82, 21.28],
                                  [-157.83, 21.27]]]}}]}
        units = padus.read_geojson(data)
        self.assertEqual(units[0]["bbox"], (21.27, -157.83, 21.28, -157.82))


if __name__ == "__main__":
    unittest.main()
