export function animationCatalog(map, manifest) {
  const faces = Object.entries(map.faces || {}).map(([id, face]) => ({
    id,
    group: face.group,
    label: `${face.group.replaceAll('_', ' ')} ${face.name.slice(face.group.length + 1).replaceAll('_', ' · ')} · ${id.slice(0, 2)}`,
  }));
  const activities = (manifest?.activities || []).map(({id, label}) => ({id, label}));
  return {faces, activities};
}

export class AnimationPreview {
  constructor(face) { this.face = face; this.faceId = null; this.activityId = null; }
  get active() { return !!(this.faceId || this.activityId); }
  selectFace(id) { this.faceId = id || null; }
  selectActivity(id) {
    if (id) {
      const result = this.face.previewActivity(id);
      if (!result?.accepted) return false;
    } else this.face.clearPreviewActivity();
    this.activityId = id || null;
    return true;
  }
  release() { this.selectFace(null); this.selectActivity(null); }
  displayedFace(liveFaceId) { return this.faceId || liveFaceId; }
}
