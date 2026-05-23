import 'package:flutter/material.dart';
import '../models/plant.dart';
import '../services/api_service.dart' show ApiService, kBaseUrl;

class PlantDetailScreen extends StatelessWidget {
  final Plant plant;
  const PlantDetailScreen({super.key, required this.plant});

  Future<void> _confirmDelete(BuildContext context) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (_) => AlertDialog(
        title: const Text('確認刪除'),
        content: Text('刪除「${plant.name}」？'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('取消')),
          TextButton(onPressed: () => Navigator.pop(context, true), child: const Text('刪除', style: TextStyle(color: Colors.red))),
        ],
      ),
    );
    if (ok == true && plant.id != null && context.mounted) {
      try {
        await ApiService.deletePlant(plant.id!);
        if (context.mounted) Navigator.pop(context, 'deleted');
      } on Exception catch (e) {
        if (context.mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(e.toString())));
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: Text(plant.name),
        actions: [
          IconButton(
            icon: const Icon(Icons.delete_outline),
            onPressed: () => _confirmDelete(context),
          ),
        ],
      ),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          if (plant.photoUrls.isNotEmpty) ...[
            SizedBox(
              height: 200,
              child: PageView.builder(
                itemCount: plant.photoUrls.length,
                itemBuilder: (_, i) => ClipRRect(
                  borderRadius: BorderRadius.circular(12),
                  child: Image.network(
                    '$kBaseUrl${plant.photoUrls[i]}',
                    fit: BoxFit.cover,
                    errorBuilder: (_, __, ___) => const Icon(Icons.broken_image, size: 80),
                  ),
                ),
              ),
            ),
            const SizedBox(height: 16),
          ],
          _infoRow('類別', plant.category),
          _infoRow('可食用部位', plant.edibleParts.isEmpty ? '—' : plant.edibleParts),
          _infoRow('位置', '${plant.lat.toStringAsFixed(5)}, ${plant.lng.toStringAsFixed(5)}'),
          if (plant.notes.isNotEmpty) ...[
            const SizedBox(height: 12),
            const Text('備註', style: TextStyle(fontWeight: FontWeight.bold)),
            const SizedBox(height: 4),
            Text(plant.notes),
          ],
          if (plant.createdAt.isNotEmpty) ...[
            const SizedBox(height: 16),
            Text('記錄於 ${plant.createdAt}', style: const TextStyle(color: Colors.grey, fontSize: 12)),
          ],
        ],
      ),
    );
  }

  Widget _infoRow(String label, String value) => Padding(
        padding: const EdgeInsets.symmetric(vertical: 6),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            SizedBox(width: 90, child: Text(label, style: const TextStyle(fontWeight: FontWeight.bold))),
            Expanded(child: Text(value)),
          ],
        ),
      );
}
