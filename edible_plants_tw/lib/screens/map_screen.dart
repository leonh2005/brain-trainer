import 'package:flutter/material.dart';
import 'package:flutter_map/flutter_map.dart';
import 'package:geolocator/geolocator.dart';
import 'package:latlong2/latlong.dart';
import '../models/plant.dart';
import '../services/api_service.dart';
import 'add_plant_screen.dart';
import 'plant_detail_screen.dart';

class MapScreen extends StatefulWidget {
  const MapScreen({super.key});

  @override
  State<MapScreen> createState() => _MapScreenState();
}

class _MapScreenState extends State<MapScreen> {
  final _mapCtrl = MapController();
  List<Plant> _plants = [];
  bool _loading = true;

  // 雙北市中心
  static const _defaultCenter = LatLng(25.033, 121.565);

  @override
  void initState() {
    super.initState();
    _loadPlants();
  }

  Future<void> _loadPlants() async {
    setState(() => _loading = true);
    try {
      final plants = await ApiService.getPlants();
      if (mounted) setState(() => _plants = plants);
    } on Exception catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('無法連線：$e')));
      }
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _goToMyLocation() async {
    var perm = await Geolocator.checkPermission();
    if (perm == LocationPermission.denied) perm = await Geolocator.requestPermission();
    if (perm == LocationPermission.denied || perm == LocationPermission.deniedForever) return;
    final pos = await Geolocator.getCurrentPosition();
    _mapCtrl.move(LatLng(pos.latitude, pos.longitude), 16);
  }

  Future<void> _addPlantAtCenter() async {
    final center = _mapCtrl.camera.center;
    final result = await Navigator.push<bool>(
      context,
      MaterialPageRoute(builder: (_) => AddPlantScreen(initialLocation: center)),
    );
    if (result == true) _loadPlants();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('雙北可食植物地圖'),
        actions: [
          IconButton(icon: const Icon(Icons.refresh), onPressed: _loadPlants),
        ],
      ),
      body: Stack(
        children: [
          FlutterMap(
            mapController: _mapCtrl,
            options: MapOptions(
              initialCenter: _defaultCenter,
              initialZoom: 12,
              onLongPress: (_, latlng) async {
                final result = await Navigator.push<bool>(
                  context,
                  MaterialPageRoute(builder: (_) => AddPlantScreen(initialLocation: latlng)),
                );
                if (result == true) _loadPlants();
              },
            ),
            children: [
              TileLayer(
                urlTemplate: 'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
                userAgentPackageName: 'com.steven.edible_plants_tw',
              ),
              MarkerLayer(
                markers: _plants.map((p) => Marker(
                  point: LatLng(p.lat, p.lng),
                  width: 40,
                  height: 40,
                  child: GestureDetector(
                    onTap: () async {
                      final result = await Navigator.push<String>(
                        context,
                        MaterialPageRoute(builder: (_) => PlantDetailScreen(plant: p)),
                      );
                      if (result == 'deleted') _loadPlants();
                    },
                    child: const Icon(Icons.eco, color: Colors.green, size: 36),
                  ),
                )).toList(),
              ),
            ],
          ),
          if (_loading)
            const Center(child: CircularProgressIndicator()),
          Positioned(
            bottom: 80,
            right: 16,
            child: FloatingActionButton(
              heroTag: 'location',
              mini: true,
              onPressed: _goToMyLocation,
              child: const Icon(Icons.my_location),
            ),
          ),
        ],
      ),
      floatingActionButton: FloatingActionButton.extended(
        onPressed: _addPlantAtCenter,
        icon: const Icon(Icons.add),
        label: const Text('新增植物'),
      ),
    );
  }
}
