import 'package:flutter/material.dart';
import 'screens/map_screen.dart';

void main() {
  runApp(const EdiblePlantsApp());
}

class EdiblePlantsApp extends StatelessWidget {
  const EdiblePlantsApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: '可食植物地圖',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        colorScheme: ColorScheme.fromSeed(seedColor: Colors.green),
        useMaterial3: true,
      ),
      home: const MapScreen(),
    );
  }
}
