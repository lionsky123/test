using UnityEngine;
using UnityEngine.Rendering;
using UnityEngine.Video;

[RequireComponent(typeof(Camera))]
public sealed class PanoramaVideoPlayer : MonoBehaviour
{
    [SerializeField] private VideoClip clip;
    [SerializeField] private Shader panoramaShader;

    private const float SphereRadius = 32f;
    private VideoPlayer player;
    private RenderTexture videoTexture;
    private Material panoramaMaterial;
    private Mesh panoramaMesh;
    private GameObject panorama;
    private bool panoramaAligned;

    private void Awake()
    {
        if (panoramaShader == null)
        {
            Debug.LogError("Assign a panorama shader before playback.", this);
            return;
        }

        CreatePanorama();

        player = gameObject.AddComponent<VideoPlayer>();
        player.playOnAwake = false;
        player.source = VideoSource.VideoClip;
        player.isLooping = true;
        player.waitForFirstFrame = true;
        player.skipOnDrop = true;
        player.renderMode = VideoRenderMode.RenderTexture;
        player.audioOutputMode = VideoAudioOutputMode.Direct;
        player.prepareCompleted += StartPlayback;
        player.errorReceived += LogVideoError;
    }

    private void Start()
    {
        PlayDefault();
    }

    public void PlayDefault()
    {
        Play(clip);
    }

    public void Play(VideoClip nextClip)
    {
        if (player == null || nextClip == null)
        {
            Debug.LogError("The panorama player needs a video clip and shader.", this);
            return;
        }

        player.Stop();
        panorama.SetActive(false);
        if (videoTexture == null || videoTexture.width != nextClip.width || videoTexture.height != nextClip.height)
        {
            ReleaseVideoTexture();
            videoTexture = new RenderTexture((int)nextClip.width, (int)nextClip.height, 0, RenderTextureFormat.ARGB32)
            {
                name = "Phoenix Forest Video",
                wrapModeU = TextureWrapMode.Repeat,
                wrapModeV = TextureWrapMode.Clamp,
                filterMode = FilterMode.Bilinear
            };
            videoTexture.Create();
            panoramaMaterial.mainTexture = videoTexture;
            player.targetTexture = videoTexture;
        }

        player.clip = nextClip;
        player.Prepare();
    }

    private void CreatePanorama()
    {
        panorama = new GameObject("Phoenix Forest Panorama");
        panorama.transform.position = transform.position;
        panorama.SetActive(false);

        panoramaMesh = CreateInwardSphere(96, 48);
        panorama.AddComponent<MeshFilter>().sharedMesh = panoramaMesh;

        panoramaMaterial = new Material(panoramaShader);
        var sphereRenderer = panorama.AddComponent<MeshRenderer>();
        sphereRenderer.sharedMaterial = panoramaMaterial;
        sphereRenderer.shadowCastingMode = ShadowCastingMode.Off;
        sphereRenderer.receiveShadows = false;
        sphereRenderer.lightProbeUsage = LightProbeUsage.Off;
        sphereRenderer.reflectionProbeUsage = ReflectionProbeUsage.Off;
    }

    private void StartPlayback(VideoPlayer preparedPlayer)
    {
        if (!preparedPlayer.isPrepared)
            return;

        // Align once so switching clips preserves the viewer's direction within the panorama.
        if (!panoramaAligned)
        {
            var forward = Vector3.ProjectOnPlane(transform.forward, Vector3.up);
            if (forward.sqrMagnitude > 0.01f)
                panorama.transform.rotation = Quaternion.LookRotation(forward);
            panoramaAligned = true;
        }

        panorama.SetActive(true);
        preparedPlayer.Play();
        Debug.Log($"[Phoenix Forest 360] Playing {preparedPlayer.clip.name} at {preparedPlayer.width}x{preparedPlayer.height}.", this);
    }

    private void LateUpdate()
    {
        // The monoscopic panorama follows head position without following head rotation.
        if (panorama != null)
            panorama.transform.position = transform.position;
    }

    private void LogVideoError(VideoPlayer source, string message)
    {
        Debug.LogError($"[Phoenix Forest 360] {source.clip.name}: {message}", this);
    }

    // Equirectangular UV layout from Discover's Phoenix360VideoExperience.
    private static Mesh CreateInwardSphere(int longitudeSegments, int latitudeSegments)
    {
        int stride = longitudeSegments + 1;
        var vertices = new Vector3[stride * (latitudeSegments + 1)];
        var uvs = new Vector2[vertices.Length];
        var triangles = new int[longitudeSegments * latitudeSegments * 6];

        for (int latitude = 0; latitude <= latitudeSegments; latitude++)
        {
            float v = latitude / (float)latitudeSegments;
            float phi = v * Mathf.PI;
            float sinPhi = Mathf.Sin(phi);
            float cosPhi = Mathf.Cos(phi);
            for (int longitude = 0; longitude <= longitudeSegments; longitude++)
            {
                float u = longitude / (float)longitudeSegments;
                float theta = (u - 0.5f) * Mathf.PI * 2f;
                int vertex = latitude * stride + longitude;
                vertices[vertex] = SphereRadius * new Vector3(
                    sinPhi * Mathf.Sin(theta), cosPhi, sinPhi * Mathf.Cos(theta));
                uvs[vertex] = new Vector2(u, 1f - v);
            }
        }

        int index = 0;
        for (int latitude = 0; latitude < latitudeSegments; latitude++)
        {
            for (int longitude = 0; longitude < longitudeSegments; longitude++)
            {
                int topLeft = latitude * stride + longitude;
                int bottomLeft = topLeft + stride;
                triangles[index++] = topLeft;
                triangles[index++] = topLeft + 1;
                triangles[index++] = bottomLeft;
                triangles[index++] = topLeft + 1;
                triangles[index++] = bottomLeft + 1;
                triangles[index++] = bottomLeft;
            }
        }

        return new Mesh
        {
            name = "Phoenix Forest Inward Sphere",
            vertices = vertices,
            uv = uvs,
            triangles = triangles
        };
    }

    private void OnDestroy()
    {
        if (player != null)
        {
            player.prepareCompleted -= StartPlayback;
            player.errorReceived -= LogVideoError;
            player.Stop();
        }

        Destroy(panorama);
        Destroy(panoramaMesh);
        Destroy(panoramaMaterial);
        ReleaseVideoTexture();
    }

    private void ReleaseVideoTexture()
    {
        if (videoTexture == null)
            return;

        if (player != null)
            player.targetTexture = null;
        videoTexture.Release();
        Destroy(videoTexture);
    }
}
