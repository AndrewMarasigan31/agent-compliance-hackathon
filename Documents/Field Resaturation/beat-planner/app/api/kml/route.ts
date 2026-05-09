import { NextRequest, NextResponse } from "next/server";
import { BeatResult, BeatStore } from "@/types";

type BeatPayload = BeatResult & { orderedStores?: BeatStore[] };

function escapeXml(str: string): string {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&apos;");
}

const COLOR_TO_ICON: Record<string, string> = {
  red: "red",
  blue: "blue",
  green: "green",
  yellow: "yellow",
  purple: "purple",
  pink: "pink",
  orange: "orange",
  ltblue: "ltblue",
  brown: "brown",
  gray: "gray",
};

export async function POST(req: NextRequest) {
  const { agentName, day, beats }: { agentName: string; day: string; beats: BeatPayload[] } =
    await req.json();

  const styles = beats
    .map((beat) => {
      const icon = COLOR_TO_ICON[beat.color] || "red";
      return `<Style id="beat-${beat.beatId}">
      <IconStyle>
        <Icon><href>http://maps.google.com/mapfiles/ms/micons/${icon}.png</href></Icon>
      </IconStyle>
    </Style>`;
    })
    .join("\n  ");

  const folders = beats
    .map((beat) => {
      const storesInOrder = beat.orderedStores ?? beat.stores;
      const placemarks = storesInOrder
        .map(
          (store) => `    <Placemark>
        <name>${escapeXml(store.store_name)}</name>
        <styleUrl>#beat-${beat.beatId}</styleUrl>
        <ExtendedData>
          <Data name="Beat"><value>${escapeXml(String(beat.beatId))}</value></Data>
          <Data name="Last Order"><value>${escapeXml(store.last_delivered_date)}</value></Data>
          <Data name="Days Dormant"><value>${escapeXml(String(store.daysDormant ?? ""))}</value></Data>
          <Data name="Rejection Reason"><value>${escapeXml(store.rejectionReason)}</value></Data>
        </ExtendedData>
        <Point><coordinates>${store.long},${store.lat},0</coordinates></Point>
      </Placemark>`
        )
        .join("\n");

      return `  <Folder>
    <name>Beat ${beat.beatId} (${escapeXml(beat.gcu)})</name>
${placemarks}
  </Folder>`;
    })
    .join("\n");

  const kml = `<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <name>${escapeXml(agentName)} - ${escapeXml(day)}</name>
    <Schema name="Store" id="Store">
      <SimpleField name="Beat" type="string"/>
      <SimpleField name="Last Order" type="string"/>
      <SimpleField name="Days Dormant" type="string"/>
      <SimpleField name="Rejection Reason" type="string"/>
    </Schema>
  ${styles}
${folders}
  </Document>
</kml>`;

  const filename = `route-${agentName}-${day.toLowerCase()}.kml`;

  return new NextResponse(kml, {
    headers: {
      "Content-Type": "application/vnd.google-earth.kml+xml",
      "Content-Disposition": `attachment; filename=${filename}`,
    },
  });
}
