using System;
using UnityEngine;
namespace EvidenceFirst.Tests
{
    [Serializable] public class Node { public int value; [SerializeReference] public Node next; }
    public sealed class SampleData : ScriptableObject
    {
        public int number = 3;
        public string label = "original";
        public int[] values = { 10, 20, 30 };
        public UnityEngine.Object linked;
        [SerializeReference] public Node graph;
    }
}
