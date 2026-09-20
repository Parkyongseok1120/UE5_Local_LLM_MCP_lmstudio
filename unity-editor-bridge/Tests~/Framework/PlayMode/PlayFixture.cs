using System.Collections;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.TestTools;
namespace EvidenceFirst.FrameworkFixture
{
    public sealed class PlayFixture
    {
        [UnityTest, Category("EvidenceFirst.Pass")]
        public IEnumerator FrameAdvances() {
            Assert.IsTrue(Application.isPlaying); int start = Time.frameCount;
            yield return null; Assert.Greater(Time.frameCount, start);
        }
        [UnityTest, Category("EvidenceFirst.Cancel")]
        public IEnumerator BoundedCancellationFixture() { for (int i = 0; i < 18000; i++) yield return null; }
    }
}
