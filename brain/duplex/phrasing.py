"""Conservative phrase boundaries for incremental speech text."""
import re

ABBREVIATIONS={'mr','mrs','ms','dr','prof','st','vs','e.g','i.e'}


def next_phrase(text):
    """Return a complete sentence/clause, or a bounded whole-word fallback."""
    for match in re.finditer(r'[.!?,;:]',text):
        i=match.start();mark=text[i]
        before=text[:i+1]
        following=text[i+1:i+2]
        if mark in '.:,':
            if i and text[i-1].isdigit() and (not following or following.isdigit()):
                continue
        if mark=='.':
            word=re.search(r'([A-Za-z.]+)\.$',before)
            if word and (word.group(1).lower() in ABBREVIATIONS or len(word.group(1))==1):
                continue
            if following=='.':continue
        if following and not following.isspace() and following not in '\"\'!?':continue
        words=re.findall(r"\b[\w]+(?:['’][\w]+)?\b",before)
        if mark in '.!?' or (len(words)>=6 and len(before.strip())>=32):
            end=i+1
            while end<len(text) and text[end] in '\"\'!?':end+=1
            while end<len(text) and text[end].isspace():end+=1
            return end,'sentence' if mark in '.!?' else 'clause'
    # Avoid holding an unpunctuated paragraph indefinitely. Never split a word,
    # and preserve the original punctuation and the same synthesis context.
    if len(text)>140:
        end=text.rfind(' ',0,141)
        if end>=80:return end+1,'word_limit'
    return None
