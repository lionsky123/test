using UnityEngine;
using UnityEngine.EventSystems;
using UnityEngine.InputSystem;
using UnityEngine.InputSystem.UI;
using UnityEngine.InputSystem.XR;
using UnityEngine.UI;
using UnityEngine.Video;

[RequireComponent(typeof(PanoramaVideoPlayer))]
public sealed class PanoramaVideoMenu : MonoBehaviour
{
    [SerializeField] private VideoClip argusClip;
    [SerializeField] private Shader controllerRayShader;

    private Button argusButton;
    private Button xiaButton;
    private Outline argusHover;
    private Outline xiaHover;
    private GameObject panel;
    private RectTransform panelRect;
    private GameObject eventSystemObject;
    private InputActionAsset actions;
    private InputActionReference[] inputReferences;
    private Material rayMaterial;
    private PanoramaControllerPointer leftPointer;
    private PanoramaControllerPointer rightPointer;

    private void Start()
    {
        var player = GetComponent<PanoramaVideoPlayer>();
        var camera = GetComponent<Camera>();
        panel = new GameObject("Video selection", typeof(RectTransform), typeof(Canvas));
        panelRect = (RectTransform)panel.transform;
        panelRect.SetParent(transform, false);
        panelRect.localPosition = new Vector3(0f, -0.54f, 1.35f);
        panelRect.localScale = Vector3.one * 0.0015f;
        panelRect.sizeDelta = new Vector2(356f, 72f);

        var canvas = panel.GetComponent<Canvas>();
        canvas.renderMode = RenderMode.WorldSpace;
        canvas.worldCamera = camera;
        panel.AddComponent<GraphicRaycaster>();

        argusButton = CreateButton("Argus", -94f, out argusHover);
        xiaButton = CreateButton("Xia", 94f, out xiaHover);
        argusButton.onClick.AddListener(() =>
        {
            player.Play(argusClip);
            UpdateSelection(argusButton);
        });
        xiaButton.onClick.AddListener(() =>
        {
            player.PlayDefault();
            UpdateSelection(xiaButton);
        });
        argusButton.interactable = argusClip != null;
        UpdateSelection(xiaButton);
        CreateEventSystem();

        rayMaterial = new Material(controllerRayShader);
        leftPointer = new PanoramaControllerPointer("Left", rayMaterial);
        rightPointer = new PanoramaControllerPointer("Right", rayMaterial);
    }

    private void LateUpdate()
    {
        leftPointer.Update(XRController.leftHand, panelRect, argusButton, xiaButton);
        rightPointer.Update(XRController.rightHand, panelRect, argusButton, xiaButton);
        argusHover.enabled = leftPointer.Hovered == argusButton || rightPointer.Hovered == argusButton;
        xiaHover.enabled = leftPointer.Hovered == xiaButton || rightPointer.Hovered == xiaButton;
    }

    private Button CreateButton(string label, float x, out Outline hoverOutline)
    {
        var root = new GameObject(label, typeof(RectTransform), typeof(Image), typeof(Button));
        var rect = (RectTransform)root.transform;
        rect.SetParent(panel.transform, false);
        rect.sizeDelta = new Vector2(168f, 72f);
        rect.anchoredPosition = new Vector2(x, 0f);

        hoverOutline = root.AddComponent<Outline>();
        hoverOutline.effectColor = new Color32(107, 255, 193, 255);
        hoverOutline.effectDistance = new Vector2(3f, 3f);
        hoverOutline.enabled = false;

        var button = root.GetComponent<Button>();
        button.targetGraphic = root.GetComponent<Image>();
        button.navigation = new Navigation { mode = Navigation.Mode.None };

        var textObject = new GameObject("Label", typeof(RectTransform), typeof(Text));
        var textRect = (RectTransform)textObject.transform;
        textRect.SetParent(rect, false);
        textRect.anchorMin = Vector2.zero;
        textRect.anchorMax = Vector2.one;
        textRect.offsetMin = Vector2.zero;
        textRect.offsetMax = Vector2.zero;

        var text = textObject.GetComponent<Text>();
        text.font = Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf");
        text.text = label;
        text.alignment = TextAnchor.MiddleCenter;
        text.fontSize = 26;
        text.fontStyle = FontStyle.Bold;
        text.raycastTarget = false;
        return button;
    }

    private void UpdateSelection(Button selected)
    {
        ApplyButtonColors(argusButton, argusButton == selected);
        ApplyButtonColors(xiaButton, xiaButton == selected);
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
