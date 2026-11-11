using System.Collections.Generic;
using UnityEngine;

namespace BookProduction.Runtime
{
    /// <summary>
    /// GameIR v3 hazard: walks its waypoints forward and back (ping-pong) in straight
    /// lines, <c>secondsPerCell</c> per cell, driven by fixed time so a run is repeatable.
    /// Touching the player records <c>hazard_hit:&lt;id&gt;</c> and sends the player to spawn.
    /// </summary>
    [RequireComponent(typeof(Rigidbody2D))]
    [RequireComponent(typeof(Collider2D))]
    public sealed class HazardPatrol : MonoBehaviour
    {
        [SerializeField] private string hazardId;
        [SerializeField] private Vector2[] waypoints = new Vector2[0];
        [SerializeField] private float secondsPerCell = 1f;
        [SerializeField] private GameProgress progress;
        private Rigidbody2D body;
        private float startTime;

        public string HazardId { get { return hazardId; } }
        public float SecondsPerCell { get { return secondsPerCell; } }
        public IReadOnlyList<Vector2> Waypoints { get { return waypoints; } }
        /// <summary>Patrol time used by the last fixed step.</summary>
        public float Elapsed { get { return Time.fixedTime - startTime; } }

        public void Initialize(string id, Vector2[] points, float seconds, GameProgress gameProgress)
        {
            hazardId = id;
            waypoints = points;
            secondsPerCell = seconds;
            progress = gameProgress;
        }

        /// <summary>Position after <paramref name="elapsed"/> seconds of patrol.</summary>
        public Vector2 PositionAt(float elapsed)
        {
            if (waypoints == null || waypoints.Length == 0)
            {
                return transform.position;
            }
            if (waypoints.Length == 1 || secondsPerCell <= 0f)
            {
                return waypoints[0];
            }
            int cycle = 2 * (waypoints.Length - 1);
            float steps = Mathf.Max(0f, elapsed) / secondsPerCell;
            int segment = Mathf.FloorToInt(steps);
            float fraction = steps - segment;
            segment %= cycle;
            return Vector2.Lerp(Waypoint(segment, cycle), Waypoint((segment + 1) % cycle, cycle), fraction);
        }

        private Vector2 Waypoint(int index, int cycle)
        {
            return index < waypoints.Length ? waypoints[index] : waypoints[cycle - index];
        }

        private void Awake()
        {
            body = GetComponent<Rigidbody2D>();
            if (progress == null)
            {
                progress = FindFirstObjectByType<GameProgress>();
            }
        }

        private void OnEnable()
        {
            startTime = Time.fixedTime;
        }

        private void FixedUpdate()
        {
            if (body != null)
            {
                body.MovePosition(PositionAt(Time.fixedTime - startTime));
            }
        }

        private void OnTriggerEnter2D(Collider2D other)
        {
            TopDownPlayerController player = other.GetComponent<TopDownPlayerController>();
            if (player == null || progress == null)
            {
                return;
            }
            progress.RecordHazardHit(hazardId);
            player.Respawn();
        }
    }
}
