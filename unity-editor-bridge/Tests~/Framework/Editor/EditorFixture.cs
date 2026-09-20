using NUnit.Framework;
namespace EvidenceFirst.FrameworkFixture
{
    public sealed class EditorFixture
    {
        [Test, Category("EvidenceFirst.Pass")]
        public void Arithmetic() { Assert.AreEqual(4, 2 + 2); }
        [Test, Category("EvidenceFirst.Fail")]
        public void DeliberateFailure() { Assert.Fail("Expected negative fixture: adapter must report failure, not success"); }
    }
}
