using Newtonsoft.Json.Linq;
using UnityEngine;
using UnityEngine.EventSystems;
using EvidenceFirst.Debugging;
namespace EvidenceFirst.Adapters
{
    public sealed class UIObservationProbe : MonoBehaviour, IPointerClickHandler, ISubmitHandler, ISelectHandler, IDeselectHandler
    {
        public double Until;
        void Send(string kind) { if (Time.realtimeSinceStartupAsDouble <= Until) DebugRegistry.Emit(kind, new JObject { ["frame"] = Time.frameCount }, gameObject, EventSystem.current?.currentSelectedGameObject); }
        void Update() { if (Time.realtimeSinceStartupAsDouble > Until) Destroy(this); }
        public void OnPointerClick(PointerEventData e) { Send("ui.pointer_click"); }
        public void OnSubmit(BaseEventData e) { Send("ui.submit"); }
        public void OnSelect(BaseEventData e) { Send("ui.select"); }
        public void OnDeselect(BaseEventData e) { Send("ui.deselect"); }
    }
}
