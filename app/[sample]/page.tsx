import { notFound, permanentRedirect } from "next/navigation";
export default async function LegacyPage({ params }: { params: Promise<{ sample: string }> }) {
  const { sample } = await params;
  if (sample === "field-guide") permanentRedirect("/docs");
  if (["showcase", "workbench", "editorial"].includes(sample)) permanentRedirect("/");
  notFound();
}
