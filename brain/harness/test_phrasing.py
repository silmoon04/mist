import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from duplex.phrasing import next_phrase

class Tests(unittest.TestCase):
    def test_useful_clause(self):
        s='I can keep the beginning quite short, and explain the rest later.'
        n,kind=next_phrase(s);self.assertEqual(s[:n],'I can keep the beginning quite short, ');self.assertEqual(kind,'clause')
    def test_short_preface_held(self):
        self.assertIsNone(next_phrase('Well, I think'))
    def test_decimal_and_unit(self):
        self.assertIsNone(next_phrase('The setting is 2.'))
        self.assertIsNone(next_phrase('The setting is 2.5 mm'))
    def test_abbreviation(self):
        self.assertIsNone(next_phrase('Ask Dr. Smith'))
    def test_sentence_inside_delta(self):
        s='That worked! Now we can try the next step.'
        n,kind=next_phrase(s);self.assertEqual(s[:n],'That worked! ')
    def test_fallback_preserves_words(self):
        s='This is a longer unpunctuated passage with words kept together '*4
        n,kind=next_phrase(s);self.assertEqual(kind,'word_limit');self.assertEqual(s[n-1],' ');self.assertLessEqual(n,141)
    def test_fragmentation_is_lossless(self):
        s='Ask Dr. Smith about 2.5 mm, then wait. I can keep the beginning quite short, and finish later.'
        for size in (1,3,7,1000):
            pending='';parts=[]
            for i in range(0,len(s),size):
                pending+=s[i:i+size]
                while (b:=next_phrase(pending)) is not None:
                    n,_=b;parts.append(pending[:n]);pending=pending[n:]
            self.assertEqual(''.join(parts)+pending,s)

if __name__=='__main__':unittest.main(verbosity=2)
