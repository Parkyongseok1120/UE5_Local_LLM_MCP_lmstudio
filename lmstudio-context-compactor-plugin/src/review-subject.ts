/** Reporting identity only. This never grants source or edit authority. */
export function subjectLeaf(identifier: string): string { return identifier.split("::").pop()!; }
export function subjectToken(identifier: string): RegExp {
  return new RegExp(`(?<![\\w:])${identifier.replace(/[.*+?^${}()|[\]\\]/gu, "\\$&")}(?![\\w:])`, "u");
}
type Subject = { identifier: string; bundle?: { path: string } };
export function mentionsSubject(text: string, row: Subject, peers: readonly Subject[]): boolean {
  if (row.identifier.includes("::") && subjectToken(row.identifier).test(text)) return true;
  const leaf = subjectLeaf(row.identifier);
  if (!subjectToken(leaf).test(text)) return false;
  const identities = new Set(peers.filter(peer => subjectLeaf(peer.identifier) === leaf)
    .map(peer => `${peer.identifier}\0${peer.bundle?.path || ""}`));
  if (identities.size <= 1) return true;
  const file = row.bundle?.path.replace(/\\/gu, "/").split("/").pop();
  const owner = row.identifier.includes("::") ? row.identifier.split("::").slice(0, -1).join("::") : null;
  return Boolean(file && text.includes(file) || owner && subjectToken(owner).test(text));
}
