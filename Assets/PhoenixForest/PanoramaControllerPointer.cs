using System;
using UnityEngine;
using UnityEngine.InputSystem.Controls;
using UnityEngine.InputSystem.XR;
using UnityEngine.Rendering;
using UnityEngine.UI;
using UnityEngine.XR;

internal sealed class PanoramaControllerPointer : IDisposable
{
    private const float RayLength = 4f;
    private const float PressThreshold = 0.55f;
    private const float ReleaseThreshold = 0.35f;
    private const InputTrackingState RequiredTracking = InputTrackingState.Position | InputTrackingState.Rotation;

    private readonly string hand;
    private readonly LineRenderer line;
    private readonly MaterialPropertyBlock rayColor = new MaterialPropertyBlock();

    private XRController controller;
    private PoseControl aim;
    private AxisControl trigger;
    private ButtonControl triggerButton;
    private bool triggerHeld;

    public Button Hovered { get; private set; }

    public PanoramaControllerPointer(string hand, Material material)
    {
        this.hand = hand;
        line = new GameObject($"{hand} controller ray").AddComponent<LineRenderer>();
        line.sharedMaterial = material;
        line.useWorldSpace = true;
        line.alignment = LineAlignment.View;
        line.positionCount = 2;
        line.startWidth = 0.004f;
        line.endWidth = 0.004f;
        line.numCapVertices = 4;
        line.shadowCastingMode = ShadowCastingMode.Off;
        line.receiveShadows = false;
        line.enabled = false;
    }

    public void Update(XRController device, RectTransform panel, Button argus, Button xia)
    {
        if (controller != device)
            SetController(device);

        Hovered = null;
        line.enabled = TryGetRay(out var ray);
        if (line.enabled)
        {
            // Work in the canvas plane, independently of which eye the camera renders.
            float distance = RayLength;
            var plane = new Plane(panel.forward, panel.position);
            if (plane.Raycast(ray, out float hitDistance) && hitDistance <= RayLength)
            {
                var hit = ray.GetPoint(hitDistance);
                Hovered = HitButton(argus, hit) ? argus : HitButton(xia, hit) ? xia : null;
                distance = Mathf.Max(0f, hitDistance - 0.004f);
            }

            line.SetPosition(0, ray.origin);
            line.SetPosition(1, ray.GetPoint(distance));
            line.endWidth = Hovered != null ? 0.012f : 0.003f;
            rayColor.SetColor("_BaseColor", Hovered != null ? new Color32(107, 255, 193, 255) : Color.white);
            line.SetPropertyBlock(rayColor);
        }

        float triggerValue = triggerButton?.isPressed == true ? 1f : (trigger?.ReadValue() ?? 0f);
        if (triggerValue < ReleaseThreshold)
        {
            triggerHeld = false;
        }
        else if (!triggerHeld && triggerValue >= PressThreshold)
        {
            triggerHeld = true;
            Debug.Log($"[Phoenix Forest XR] {hand} trigger: {(Hovered != null ? Hovered.name : "no button")}.");
            if (Hovered != null)
                Hovered.onClick.Invoke();
        }
    }

    private void SetController(XRController device)
    {
        controller = device;
        aim = device?.TryGetChildControl<PoseControl>("pointer");
        trigger = device?.TryGetChildControl<AxisControl>("trigger");
        triggerButton = device?.TryGetChildControl<ButtonControl>("triggerPressed");
        // Wait for release if the trigger was already held when the controller connected.
        triggerHeld = true;
        if (device != null)
            Debug.Log($"[Phoenix Forest XR] {hand} controller: {device.layout}.");
    }

    private bool TryGetRay(out Ray ray)
    {
        ray = default;
        if (controller == null || !controller.enabled)
            return false;

        if (aim != null)
        {
            var pose = aim.ReadValue();
            if (pose.isTracked && (pose.trackingState & RequiredTracking) == RequiredTracking)
            {
                ray = new Ray(pose.position, pose.rotation * Vector3.forward);
                return true;
            }
        }

        if (!controller.isTracked.isPressed ||
            ((InputTrackingState)controller.trackingState.ReadValue() & RequiredTracking) != RequiredTracking)
            return false;

        ray = new Ray(controller.devicePosition.ReadValue(), controller.deviceRotation.ReadValue() * Vector3.forward);
        return true;
    }

    private static bool HitButton(Button button, Vector3 hit)
    {
        var rect = (RectTransform)button.transform;
        return button.IsInteractable() && rect.rect.Contains(rect.InverseTransformPoint(hit));
    }

    public void Dispose()
    {
        if (line != null)
            UnityEngine.Object.Destroy(line.gameObject);
    }
}
