using UnityEngine;
using UnityEngine.Events;
using UnityEngine.EventSystems;
using UnityEngine.InputSystem;
using UnityEngine.InputSystem.UI;
using UnityEngine.InputSystem.XR;
using UnityEngine.UI;
using UnityEngine.Video;

[RequireComponent(typeof(PanoramaVideoPlayer))]
public sealed class PanoramaVideoMenu : MonoBehaviour
{
    [SerializeField] private VideoClip phoenixForestSourceClip;
    [SerializeField] private VideoClip imagine360Clip;
    [SerializeField] private VideoClip argusClip;
    [SerializeField] private VideoClip pigWatermelonSourceClip;
    [SerializeField] private VideoClip pigWatermelonImagine360Clip;
    [SerializeField] private VideoClip pigWatermelonProposedClip;
    [SerializeField] private VideoClip pigWatermelonArgusClip;
    [SerializeField] private Shader controllerRayShader;

    private static readonly string[] ButtonLabels = { "Original (2D)", "Imagine360", "Proposed workflow", "Argus" };
    private static readonly float[] ButtonWidths = { 154f, 170f, 236f, 126f };

    private Button[] buttons;
    private Outline[] hoverOutlines;
    private GameObject panel;
    private RectTransform panelRect;
    private GameObject eventSystemObject;
    private InputActionAsset actions;
    private InputActionReference[] inputReferences;
    private Material rayMaterial;
    private PanoramaControllerPointer leftPointer;
    private PanoramaControllerPointer rightPointer;
    private Font font;

    private void Start()
    {
        var player = GetComponent<PanoramaVideoPlayer>();
        var camera = GetComponent<Camera>();
        panel = new GameObject("Video selection", typeof(RectTransform), typeof(Canvas));
        panelRect = (RectTransform)panel.transform;
        panelRect.SetParent(transform, false);
        panelRect.localPosition = new Vector3(0f, -0.55f, 1.5f);
        panelRect.localScale = Vector3.one * 0.0015f;
        panelRect.sizeDelta = new Vector2(752f, 222f);

        var canvas = panel.GetComponent<Canvas>();
        canvas.renderMode = RenderMode.WorldSpace;
        canvas.worldCamera = camera;
        panel.AddComponent<GraphicRaycaster>();

        var background = panel.AddComponent<Image>();
        background.color = new Color32(13, 22, 29, 225);
        background.raycastTarget = false;
        font = Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf");

        buttons = new Button[2 * ButtonLabels.Length];
        hoverOutlines = new Outline[buttons.Length];
        CreateGroup("PhoenixForest", 52f, 0, player,
            new[] { phoenixForestSourceClip, imagine360Clip, player.DefaultClip, argusClip });
        CreateGroup("PigWatermelon", -52f, ButtonLabels.Length, player,
            new[] { pigWatermelonSourceClip, pigWatermelonImagine360Clip, pigWatermelonProposedClip, pigWatermelonArgusClip });
        UpdateSelection(player.DefaultClip != null ? buttons[2] : null);
        CreateEventSystem();

        rayMaterial = new Material(controllerRayShader);
        leftPointer = new PanoramaControllerPointer("Left", rayMaterial);
        rightPointer = new PanoramaControllerPointer("Right", rayMaterial);
    }

    private void LateUpdate()
    {
        leftPointer.Update(XRController.leftHand, panelRect, buttons);
        rightPointer.Update(XRController.rightHand, panelRect, buttons);
        for (int i = 0; i < buttons.Length; i++)
        {
            var button = buttons[i];
            hoverOutlines[i].enabled = leftPointer.Hovered == button || rightPointer.Hovered == button;
        }
    }

    private void CreateGroup(string title, float rowY, int firstIndex, PanoramaVideoPlayer player, VideoClip[] clips)
    {
        var heading = CreateLabel(panelRect, title, 24, TextAnchor.MiddleLeft);
        heading.rectTransform.anchoredPosition = new Vector2(0f, rowY + 34f);
        heading.rectTransform.sizeDelta = new Vector2(722f, 30f);

        float left = -361f;
        for (int column = 0; column < clips.Length; column++)
        {
            VideoClip video = clips[column];
            bool panoramic = column != 0;
            int index = firstIndex + column;
            float width = ButtonWidths[column];
            string label = ButtonLabels[column];
            buttons[index] = CreateButton($"{title} - {label}", label,
                new Vector2(left + width / 2f, rowY - 13f), width,
                () => player.Play(video, panoramic), out hoverOutlines[index]);
            buttons[index].interactable = video != null;
            left += width + 12f;
        }
    }

    private Button CreateButton(string name, string label, Vector2 position, float width, UnityAction playVideo,
        out Outline hoverOutline)
    {
        var root = new GameObject(name, typeof(RectTransform), typeof(Image), typeof(Button));
        var rect = (RectTransform)root.transform;
        rect.SetParent(panel.transform, false);
        rect.sizeDelta = new Vector2(width, 64f);
        rect.anchoredPosition = position;

        hoverOutline = root.AddComponent<Outline>();
        hoverOutline.effectColor = new Color32(107, 255, 193, 255);
        hoverOutline.effectDistance = new Vector2(3f, 3f);
        hoverOutline.enabled = false;

        var button = root.GetComponent<Button>();
        button.targetGraphic = root.GetComponent<Image>();
        button.navigation = new Navigation { mode = Navigation.Mode.None };
        button.onClick.AddListener(() =>
        {
            playVideo();
            UpdateSelection(button);
        });

        var textRect = CreateLabel(rect, label, 24, TextAnchor.MiddleCenter).rectTransform;
        textRect.anchorMin = Vector2.zero;
        textRect.anchorMax = Vector2.one;
        textRect.offsetMin = Vector2.zero;
        textRect.offsetMax = Vector2.zero;
        return button;
    }

    private Text CreateLabel(RectTransform parent, string label, int size, TextAnchor alignment)
    {
        var text = new GameObject("Label", typeof(RectTransform), typeof(Text)).GetComponent<Text>();
        text.rectTransform.SetParent(parent, false);
        text.font = font;
        text.text = label;
        text.alignment = alignment;
        text.fontSize = size;
        text.fontStyle = FontStyle.Bold;
        text.color = Color.white;
        text.raycastTarget = false;
        return text;
    }

    private void UpdateSelection(Button selected)
    {
        foreach (var button in buttons)
            ApplyButtonColors(button, button == selected);
    }

    private static void ApplyButtonColors(Button button, bool selected)
    {
        var colors = button.colors;
        colors.normalColor = selected ? new Color32(24, 101, 76, 255) : new Color32(28, 43, 53, 255);
        colors.highlightedColor = new Color32(53, 123, 101, 255);
        colors.pressedColor = new Color32(35, 79, 66, 255);
        colors.fadeDuration = 0f;
        button.colors = colors;
    }

    private void CreateEventSystem()
    {
        actions = ScriptableObject.CreateInstance<InputActionAsset>();
        var map = actions.AddActionMap("Panorama UI");
        var point = map.AddAction("Point", InputActionType.PassThrough, "<Mouse>/position", expectedControlLayout: "Vector2");
        var click = map.AddAction("Click", InputActionType.PassThrough, "<Mouse>/leftButton", expectedControlLayout: "Button");
        point.wantsInitialStateCheck = true;

        inputReferences = new[]
        {
            InputActionReference.Create(point), InputActionReference.Create(click)
        };
        eventSystemObject = new GameObject("Video menu events");
        eventSystemObject.SetActive(false);
        eventSystemObject.AddComponent<EventSystem>().sendNavigationEvents = false;
        var input = eventSystemObject.AddComponent<InputSystemUIInputModule>();
        input.actionsAsset = actions;
        input.point = inputReferences[0];
        input.leftClick = inputReferences[1];
        eventSystemObject.SetActive(true);
    }

    private void OnDestroy()
    {
        if (eventSystemObject != null)
        {
            eventSystemObject.SetActive(false);
            Destroy(eventSystemObject);
            foreach (var reference in inputReferences)
                Destroy(reference);
        }
        leftPointer?.Dispose();
        rightPointer?.Dispose();
        Destroy(rayMaterial);
        Destroy(actions);
        Destroy(panel);
    }
}
