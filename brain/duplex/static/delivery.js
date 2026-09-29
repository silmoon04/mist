// Conservative wording cues, not emotion recognition. Explicit faces take priority.
export function replyExpression(text){
  const first=typeof text==='string'?text.trim().toLowerCase().replaceAll('’',"'"):'';
  if(/^(?:i(?:'m| am) (?:sorry|afraid)|no (?:rush|advice)|that sounds (?:hard|rough)|fair correction)\b/.test(first))return 'neutral';
  if(/^(?:that worked|nice work|well done|i(?:'m| am) (?:really )?glad|we did it|that's (?:great|good|wonderful)|that is (?:great|good|wonderful))\b/.test(first))return 'happy';
  if(/^(?:good question|i wonder|let's find out|that(?:'s| is) interesting|interesting[,!.])/.test(first))return 'curious';
  if(/^(?:i(?:'m| am) not sure|i don't (?:know|have enough information)|i can't tell (?:yet|from))\b/.test(first))return 'uncertain';
  return 'neutral';
}
